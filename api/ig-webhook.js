// Instagram webhook alicisi + kuralli otomatik cevaplayici: geoni.ai/api/ig-webhook
// - GET: Meta abonelik dogrulamasi (hub.challenge yankilanir)
// - POST: imza dogrulanir (X-Hub-Signature-256), olay ig_events'e yazilir;
//   ig_autoreply_enabled='true' ise yorum/DM'lere SABLONLU tek-seferlik cevap.
// Tum ayarlar Supabase app_config'te (service-role only): acma/kapama anahtari,
// sablon metinler, IG token — deploy'suz degistirilebilir. Tekillestirme
// ig_replies tablosuyla: ayni yoruma bir kez, ayni gonderene gunde bir DM.
import crypto from 'crypto';

const GRAPH = 'https://graph.instagram.com/v23.0';

function sb(path, init = {}) {
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  return fetch(`${process.env.SUPABASE_URL}/rest/v1/${path}`, {
    ...init,
    headers: {
      apikey: key,
      Authorization: `Bearer ${key}`,
      'Content-Type': 'application/json',
      ...(init.headers || {}),
    },
  });
}

async function getConfig() {
  const keys = 'ig_verify_token,ig_app_secret,ig_access_token,ig_self_id,ig_autoreply_enabled,ig_autoreply_comment_text,ig_autoreply_dm_text';
  const r = await sb(`app_config?key=in.(${keys})&select=key,value`);
  const rows = r.ok ? await r.json() : [];
  return Object.fromEntries(rows.map((x) => [x.key, x.value]));
}

/** Ilk kez mi? ig_replies'e ekler; zaten varsa false doner (ikinci cevap yok). */
async function claimReply(targetId, kind) {
  const r = await sb('ig_replies?on_conflict=target_id', {
    method: 'POST',
    headers: { Prefer: 'resolution=ignore-duplicates,return=representation' },
    body: JSON.stringify({ target_id: targetId, kind }),
  });
  if (!r.ok) return false;
  const rows = await r.json().catch(() => []);
  return Array.isArray(rows) && rows.length > 0;
}

async function replyToComment(commentId, text, token) {
  await fetch(`${GRAPH}/${commentId}/replies`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ message: text }),
  }).catch(() => {});
}

async function sendDm(recipientId, text, token) {
  await fetch(`${GRAPH}/me/messages`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ recipient: { id: recipientId }, message: { text } }),
  }).catch(() => {});
}

function today() {
  return new Date().toISOString().slice(0, 10).replace(/-/g, '');
}

/** Olaylari isle: yorumlara ve DM'lere korumali otomatik cevap. */
async function autoReply(body, cfg) {
  if (cfg.ig_autoreply_enabled !== 'true') return;
  const token = cfg.ig_access_token;
  const self = cfg.ig_self_id;
  if (!token || !self) return;

  for (const entry of body.entry || []) {
    if (String(entry.id) === '0') continue; // Meta panel test olaylari

    // DM'ler — gercek akista entry.messaging[], bazi bicimlerde changes[].value
    const dmEvents = [...(entry.messaging || [])];
    for (const c of entry.changes || []) {
      if (c.field === 'messages' && c.value) dmEvents.push(c.value);
    }
    for (const m of dmEvents) {
      const sender = m.sender?.id && String(m.sender.id);
      const isEcho = !!m.message?.is_echo;
      const hasText = !!m.message?.text || !!m.message?.mid;
      if (!sender || sender === self || isEcho || !hasText) continue;
      if (await claimReply(`dm:${sender}:${today()}`, 'dm')) {
        await sendDm(sender, cfg.ig_autoreply_dm_text || '', token);
      }
    }

    // Yorumlar
    for (const c of entry.changes || []) {
      if (c.field !== 'comments' || !c.value) continue;
      const v = c.value;
      const commentId = v.id && String(v.id);
      const from = v.from?.id && String(v.from.id);
      if (!commentId || !from || from === self) continue; // kendi yorumlarimiza cevap yok
      if (await claimReply(`cm:${commentId}`, 'comment')) {
        await replyToComment(commentId, cfg.ig_autoreply_comment_text || '', token);
      }
    }
  }
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

  // Ham olaylari sakla
  const rows = [];
  for (const entry of body.entry || []) {
    const fields = (entry.changes || []).map((c) => c.field);
    if (entry.messaging) fields.push('messaging');
    rows.push({ object: body.object || null, field: fields.join(',') || null, entry });
  }
  if (rows.length) {
    await sb('ig_events', {
      method: 'POST',
      headers: { Prefer: 'return=minimal' },
      body: JSON.stringify(rows),
    }).catch(() => {});
  }

  // Otomatik cevap — hata verse bile 200 doneriz (Meta tekrar denemesin)
  try { await autoReply(body, cfg); } catch { /* sessiz */ }

  res.statusCode = 200;
  return res.end('ok');
}
