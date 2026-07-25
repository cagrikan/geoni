import crypto from 'node:crypto';

/**
 * Creator/elci basvurusu (/isbirligi formu).
 *
 * NEDEN SUNUCU TARAFI: creator_applications tablosunda RLS acik ve HIC politika
 * yok — anon rol ne okur ne yazar. Tek yazma yolu bu fonksiyon + service key.
 * Boylece tablo internete hic acilmaz.
 */

const ALLOWED_ORIGINS = new Set(['https://geoni.ai', 'https://www.geoni.ai']);
const MODELS = new Set(['barter', 'expert']);
const BANDS = new Set(['<1k', '1-10k', '10-50k', '50-250k', '250k+']);

// Alan basina ust sinir: govde sismesi + mail kotasi korumasi.
const LIMITS = { name: 80, handle: 60, note: 600, email: 254 };

function clean(v, max) {
  if (typeof v !== 'string') return '';
  // Kontrol karakterleri (satir sonu haric) temizlenir: mail govdesine ya da
  // admin paneline gorunmez karakter/CRLF enjeksiyonu girmesin.
  return v.replace(/[\u0000-\u0008\u000B-\u001F\u007F]/g, '').trim().slice(0, max);
}

function esc(v) {
  return String(v == null ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function isValidEmail(e) {
  return typeof e === 'string' && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(e) && e.length <= LIMITS.email;
}

function normalizeHandle(h) {
  // "@ad", "ad", "instagram.com/ad", "https://www.instagram.com/ad/" -> "@ad"
  let s = clean(h, LIMITS.handle).replace(/^https?:\/\//i, '').replace(/^www\./i, '');
  s = s.replace(/^(?:instagram|tiktok|x|twitter|youtube)\.com\/(?:@)?/i, '');
  // KUCUK HARF sart: benzersizlik kisiti duz `handle` kolonunda. Normalize
  // etmezsek "@Ali" ve "@ali" IKI AYRI basvuru satiri acar (tekilleştirme
  // caliskan gorunur ama calismaz) — canli testte @DENEME_Creator boyle kacti.
  s = s.split(/[/?#]/)[0].replace(/^@+/, '').trim().toLowerCase();
  return s ? '@' + s : '';
}

function clientIp(req) {
  const xf = req.headers['x-forwarded-for'];
  return (Array.isArray(xf) ? xf[0] : String(xf || '')).split(',')[0].trim() || 'yok';
}

/** IP'yi duz saklamayiz — biber (pepper) ile hash. Amac yalniz hiz siniri. */
function hashIp(ip) {
  const pepper = process.env.IP_HASH_PEPPER || process.env.SUPABASE_SERVICE_ROLE_KEY || 'geoni';
  return crypto.createHmac('sha256', pepper).update(ip).digest('hex').slice(0, 32);
}

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

async function notify(row) {
  if (!process.env.RESEND_API_KEY) return;
  const l = (k, v) => `<p style="margin:4px 0"><b>${k}:</b> ${esc(v) || '—'}</p>`;
  await fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: { Authorization: `Bearer ${process.env.RESEND_API_KEY}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      from: 'GEONI <mail@geoni.ai>',
      to: ['mail@cagricakir.com.tr'],
      // Basvuran metnini reply-to'ya koymuyoruz: dogrulanmamis adres, sahte
      // gonderen izlenimi yaratir. Adres govdede duruyor.
      subject: `Creator basvurusu: ${row.handle}`,
      html: `<h2 style="font:600 16px system-ui">Yeni creator basvurusu</h2>
${l('Ad', row.name)}${l('Hesap', row.handle)}${l('Takipci', row.follower_band)}
${l('Model', row.model === 'expert' ? 'Uzman-Ortak' : 'Barter')}${l('E-posta', row.email)}
${l('Not', row.note)}
<p style="margin-top:14px;font:13px system-ui;color:#666">Admin panel -> Creator sekmesinden durumu isaretle.</p>`,
    }),
  }).catch(() => {});
}

export default async function handler(req, res) {
  const origin = req.headers.origin;
  if (ALLOWED_ORIGINS.has(origin)) res.setHeader('Access-Control-Allow-Origin', origin);
  res.setHeader('Vary', 'Origin');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();
  if (req.method !== 'POST') return res.status(405).json({ error: 'method' });
  if (!process.env.SUPABASE_URL || !process.env.SUPABASE_SERVICE_ROLE_KEY) {
    return res.status(500).json({ error: 'config' });
  }

  const b = req.body || {};
  // Bal kupu: gercek kullanici bu alani goremez (CSS ile gizli), bot doldurur.
  // Basarili gibi donariz ki bot yeniden denemesin.
  if (clean(b.website, 50)) return res.status(200).json({ ok: true });

  const name = clean(b.name, LIMITS.name);
  const handle = normalizeHandle(b.handle);
  const note = clean(b.note, LIMITS.note);
  const email = isValidEmail(b.email) ? b.email.trim() : null;
  const model = MODELS.has(b.model) ? b.model : null;
  const band = BANDS.has(b.follower_band) ? b.follower_band : null;

  if (name.length < 2) return res.status(400).json({ error: 'name' });
  if (handle.length < 3) return res.status(400).json({ error: 'handle' });

  const ipHash = hashIp(clientIp(req));

  try {
    // Hiz siniri: ayni IP'den saatte 5 basvuru. Ayni @hesabin tekrari zaten
    // benzersiz indeksle upsert'e donuyor, bu sinir FARKLI hesap yagmurunu keser.
    const since = new Date(Date.now() - 3600e3).toISOString();
    const rl = await sb(
      `creator_applications?select=id&ip_hash=eq.${ipHash}&created_at=gte.${since}&limit=6`
    );
    if (rl.ok) {
      const rows = await rl.json().catch(() => []);
      if (Array.isArray(rows) && rows.length >= 5) return res.status(429).json({ error: 'rate' });
    }

    const row = {
      name, handle, note: note || null, email, model, follower_band: band,
      ip_hash: ipHash, user_agent: clean(req.headers['user-agent'], 200) || null,
      updated_at: new Date().toISOString(),
    };

    // Tekrar gonderim yeni satir ACMAZ, mevcut basvuruyu tazeler. status/user_id
    // /referral_code KORUNUR — kabul edilmis bir creator formu tekrar doldurunca
    // 'new'e geri dusmesin (kabulu ve referral kodunu kaybetmesin).
    const ins = await sb('creator_applications?on_conflict=handle', {
      method: 'POST',
      headers: { Prefer: 'resolution=merge-duplicates,return=representation' },
      body: JSON.stringify(row),
    });
    if (!ins.ok) {
      const txt = await ins.text().catch(() => '');
      console.error('creator-apply insert', ins.status, txt.slice(0, 200));
      return res.status(500).json({ error: 'save' });
    }
    await notify(row);
    return res.status(200).json({ ok: true });
  } catch (e) {
    console.error('creator-apply', e);
    return res.status(500).json({ error: 'server' });
  }
}
