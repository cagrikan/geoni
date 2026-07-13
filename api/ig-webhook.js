// Instagram webhook alicisi: geoni.ai/api/ig-webhook
// - GET: Meta'nin abonelik dogrulamasi (hub.challenge yankilanir)
// - POST: DM/yorum olaylari — X-Hub-Signature-256 imzasi app secret ile
//   dogrulanir, ham olay ig_events tablosuna yazilir.
// Verify token ve app secret repo'da DEGIL, Supabase app_config'te durur
// (bu Vercel projesinde SUPABASE_URL + SERVICE_ROLE_KEY zaten tanimli).
import crypto from 'crypto';

async function getConfig() {
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  const r = await fetch(
    `${url}/rest/v1/app_config?key=in.(ig_verify_token,ig_app_secret)&select=key,value`,
    { headers: { apikey: key, Authorization: `Bearer ${key}` } }
  );
  const rows = r.ok ? await r.json() : [];
  return Object.fromEntries(rows.map((x) => [x.key, x.value]));
}

export const config = { api: { bodyParser: false } };

function readRawBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}

export default async function handler(req, res) {
  const cfg = await getConfig();

  if (req.method === 'GET') {
    // Abonelik el sikismasi
    const { 'hub.mode': mode, 'hub.verify_token': token, 'hub.challenge': challenge } = req.query;
    if (mode === 'subscribe' && token && token === cfg.ig_verify_token) {
      res.statusCode = 200;
      return res.end(challenge);
    }
    res.statusCode = 403;
    return res.end('verification failed');
  }

  if (req.method !== 'POST') {
    res.statusCode = 405;
    return res.end();
  }

  const raw = await readRawBody(req);

  // Imza dogrulama: sha256=HMAC(app_secret, raw_body)
  const sig = req.headers['x-hub-signature-256'] || '';
  const expected = 'sha256=' + crypto.createHmac('sha256', cfg.ig_app_secret || '').update(raw).digest('hex');
  const valid = sig.length === expected.length &&
    crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(expected));
  if (!valid) {
    res.statusCode = 401;
    return res.end('bad signature');
  }

  let body = {};
  try { body = JSON.parse(raw.toString('utf8')); } catch { /* bos birak */ }

  // Her entry ayri satir; field degisim tipini tasir (messages/comments...)
  const rows = [];
  for (const entry of body.entry || []) {
    const fields = (entry.changes || []).map((c) => c.field);
    if (entry.messaging) fields.push('messaging');
    rows.push({
      object: body.object || null,
      field: fields.join(',') || null,
      entry,
    });
  }
  if (rows.length) {
    await fetch(`${process.env.SUPABASE_URL}/rest/v1/ig_events`, {
      method: 'POST',
      headers: {
        apikey: process.env.SUPABASE_SERVICE_ROLE_KEY,
        Authorization: `Bearer ${process.env.SUPABASE_SERVICE_ROLE_KEY}`,
        'Content-Type': 'application/json',
        Prefer: 'return=minimal',
      },
      body: JSON.stringify(rows),
    }).catch(() => {});
  }

  // Meta 200 bekler; gecikirse yeniden dener.
  res.statusCode = 200;
  return res.end('ok');
}
