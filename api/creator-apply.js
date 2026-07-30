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

/** Dogrulama token'i: duz hali YALNIZ e-postada, DB'de sha256 hash'i durur. */
function newVerifyToken() {
  const raw = crypto.randomBytes(32).toString('base64url');
  return { raw, hash: crypto.createHash('sha256').update(raw).digest('hex') };
}

async function sendVerifyMail(to, handle, raw) {
  if (!process.env.RESEND_API_KEY) return;
  const link = `https://geoni.ai/api/creator-verify?token=${encodeURIComponent(raw)}`;
  await fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: { Authorization: `Bearer ${process.env.RESEND_API_KEY}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      from: 'GEONI <mail@geoni.ai>',
      to: [to],
      subject: 'GEONI creator başvurunuzu doğrulayın',
      html: `<div style="font:15px/1.6 system-ui;max-width:520px">
<h2 style="font:600 18px system-ui">Başvurunuzu doğrulayın</h2>
<p><b>${esc(handle)}</b> için bir GEONI creator başvurusu alındı. Bu adresin size ait olduğunu onaylamak için:</p>
<p><a href="${esc(link)}" style="display:inline-block;background:#7c3aed;color:#fff;padding:11px 20px;border-radius:9px;text-decoration:none;font-weight:600">Başvurumu doğrula</a></p>
<p style="color:#666;font-size:13px">Bağlantı 24 saat geçerlidir. Bu başvuruyu siz yapmadıysanız bu e-postayı yok sayın — doğrulanmayan başvuru işleme alınmaz.</p>
</div>`,
    }),
  }).catch(() => {});
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
      subject: `${row._tekrar ? 'Creator TEKRAR gonderim' : 'Creator basvurusu'}: ${row.handle}`,
      html: `<h2 style="font:600 16px system-ui">${row._tekrar ? 'Var olan basvuru guncellendi' : 'Yeni creator basvurusu'}</h2>
${row._tekrar ? '<p style="margin:4px 0;padding:8px 10px;background:#fff4e5;border-left:3px solid #e8a33d;font:13px system-ui">Bu handle icin ZATEN basvuru vardi. ' + (row._kilitli ? 'E-posta dogrulanmis oldugu icin DEGISTIRILMEDI.' : 'E-posta guncellendi ve dogrulama sifirlandi.') + '</p>' : ''}
${l('Ad', row.name)}${l('Hesap', row.handle)}${l('Takipci', row.follower_band)}
${l('Model', row.model === 'expert' ? 'Uzman-Ortak' : 'Barter')}${l('E-posta', row.email)}
${l('E-posta dogrulandi', row._kilitli ? 'EVET (kilitli)' : 'HAYIR — dogrulama linki gonderildi')}
${l('Not', row.note)}
<p style="margin-top:14px;font:13px system-ui;color:#666">Admin panel -> Creator sekmesinden durumu isaretle. <b>Dogrulanmamis e-posta otomatik hesap eslemesine girmez.</b></p>`,
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

    // GUVENLIK (2026-07-30): bu uc KIMLIKSIZ. Upsert `on_conflict=handle`
    // oldugu icin eskiden bir baskasinin @handle'ina POST atip E-POSTASINI
    // uzerine yazmak mumkundu. Zincir kabulde kapaniyordu:
    // db.admin_decide_creator_application user_id'yi TAM DA bu e-postadan
    // esler -> saldirgan referral kodunu, sozlesmeyi ve (uzman yapilirsa)
    // is_expert yetkisini devralirdi. Iki kapi konuldu:
    //   (1) DOGRULANMIS e-posta bu uctan DEGISTIRILEMEZ (asagida).
    //   (2) Dogrulanmamis adres kabulde otomatik hesap eslemesine GIREMEZ
    //       (backend tarafi, db.py).
    let mevcut = null;
    const q = await sb(
      `creator_applications?handle=eq.${encodeURIComponent(handle)}` +
      `&select=id,email,email_verified,name,model,follower_band&limit=1`
    );
    if (q.ok) mevcut = (await q.json().catch(() => []))[0] || null;

    // Dogrulanmis bir basvurunun adresi KILITLIDIR. Degistirmek isteyen
    // (mesru kullanici dahil) destege yazar; sessizce ok doneriz ki bu uc
    // "hangi handle basvurmus / dogrulanmis" diye numaralandirilamasin.
    const adresKilitli = !!(mevcut && mevcut.email_verified);
    const yeniAdres = adresKilitli ? null : email;

    // ILK BASVURU HANDLE'I SAHIPLENIR (2026-07-30, canli testte bulundu).
    // Ilk tur duzeltme yalniz e-postayi kilitlemisti; canli sizma testinde
    // saldirgan dogrulanmis bir basvurunun ADINI "Saldirgan", MODELINI ise
    // "expert" yapabildi. Para yolu kapaliydi (user_id e-postadan gelir) ama
    // bunlar admin'in KARAR VERDIGI yuzey: panelde yanlis ad gorunur ve
    // "Uzman-Ortak" etiketi uzman yetkisi vermeye yonlendirir.
    // Dogrulanmis satirda kimlik/karar alanlari DONDURULUR; yalniz `note`
    // tazelenebilir (mesru basvuran bilgi ekleyebilsin; icerik zaten escape'li
    // ve admin maili tekrar-gonderimi acikca isaretliyor).
    // NOT: dondurulan alanlar payload'dan CIKARILAMAZ — `name` NOT NULL ve
    // varsayilansiz; PostgREST upsert'i `INSERT ... ON CONFLICT DO UPDATE`
    // uretiyor, yani satir zaten varken bile INSERT sekli gecerli olmali.
    // Cikarinca uc 500 donuyordu; veri bozulmuyordu (guvenli yon) ama meşru
    // tekrar gonderim hata aliyor ve 500-vs-200 farki "bu handle var ve
    // dogrulanmis" bilgisini SIZDIRIYORDU (numaralandirma). Bu yuzden alanlar
    // SAKLANAN degerleriyle yazilir: kisit saglanir, icerik degismez.
    const row = adresKilitli
      ? {
          name: mevcut.name, handle, note: note || null,
          model: mevcut.model, follower_band: mevcut.follower_band,
          updated_at: new Date().toISOString(),
        }
      : {
          name, handle, note: note || null, model, follower_band: band,
          ip_hash: ipHash, user_agent: clean(req.headers['user-agent'], 200) || null,
          updated_at: new Date().toISOString(),
        };

    // Adres kilitliyse email/verify alanlarina HIC dokunma (payload'da yoksa
    // merge-duplicates onlari korur). Aksi halde yeni adres yazilir ve
    // dogrulama SIFIRLANIR — adres degisince eski onay gecersizdir.
    let token = null;
    if (!adresKilitli) {
      row.email = yeniAdres;
      row.email_verified = false;
      if (yeniAdres) {
        token = newVerifyToken();
        row.verify_token_hash = token.hash;
        row.verify_expires_at = new Date(Date.now() + 24 * 3600e3).toISOString();
      } else {
        row.verify_token_hash = null;
        row.verify_expires_at = null;
      }
    }

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
    if (token && yeniAdres) await sendVerifyMail(yeniAdres, handle, token.raw);
    // Admin bildirimi: tekrar gonderim ve dogrulama durumu GORUNUR olsun ki
    // "normal gorunen" bir uzerine-yazma sessizce kabul edilmesin.
    await notify({
      ...row,
      // Dondurulmus alanlar icin maile SAKLANAN degeri yaz — admin saldirganin
      // gonderdigi adi/modeli degil, gercek basvuruyu gorsun.
      name: adresKilitli ? mevcut.name : row.name,
      model: adresKilitli ? mevcut.model : row.model,
      follower_band: adresKilitli ? mevcut.follower_band : row.follower_band,
      email: adresKilitli ? mevcut.email : yeniAdres,
      _tekrar: !!mevcut,
      _kilitli: adresKilitli,
    });
    return res.status(200).json({ ok: true });
  } catch (e) {
    console.error('creator-apply', e);
    return res.status(500).json({ error: 'server' });
  }
}
