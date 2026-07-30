import crypto from 'node:crypto';

/**
 * Creator basvurusu e-posta dogrulamasi (/api/creator-verify?token=...).
 *
 * NEDEN VAR: /api/creator-apply KIMLIKSIZ bir uc ve upsert `on_conflict=handle`
 * calisiyor. Eskiden bir baskasinin @handle'ina POST atip E-POSTASINI uzerine
 * yazmak mumkundu; kabul akisi (db.admin_decide_creator_application) user_id'yi
 * TAM DA o e-postadan esledigi icin saldirgan referral kodunu, sozlesmeyi ve
 * (uzman yapilirsa) is_expert yetkisini devralabiliyordu. Artik adres sahipligi
 * link ile kanitlanir; dogrulanmamis adres otomatik hesap eslemesine GIREMEZ.
 *
 * Token DB'de HASH'li durur (duz hali yalniz e-postada). Tek kullanimliktir:
 * dogrulanan satirda hash ve son-kullanma temizlenir.
 */

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

function sayfa(baslik, mesaj, renk) {
  return `<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>${baslik} · GEONI</title></head>
<body style="margin:0;min-height:100vh;display:grid;place-items:center;background:#0d0b14;color:#e9e6f2;font:15px/1.6 system-ui,-apple-system,Segoe UI,sans-serif">
<main style="max-width:440px;padding:32px 28px;text-align:center">
<div style="font-size:40px;line-height:1;margin-bottom:14px">${renk}</div>
<h1 style="font:700 21px system-ui;margin:0 0 10px">${baslik}</h1>
<p style="margin:0 0 22px;color:#a9a2bd">${mesaj}</p>
<a href="https://geoni.ai/isbirligi" style="display:inline-block;background:#7c3aed;color:#fff;padding:11px 22px;border-radius:9px;text-decoration:none;font-weight:600">GEONI'ye dön</a>
</main></body></html>`;
}

export default async function handler(req, res) {
  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  // Dogrulama sonucu hicbir yerde onbellege alinmasin.
  res.setHeader('Cache-Control', 'no-store');

  if (req.method !== 'GET') return res.status(405).end(sayfa('Geçersiz istek', 'Bu bağlantı yalnızca tarayıcıdan açılır.', '⚠️'));
  if (!process.env.SUPABASE_URL || !process.env.SUPABASE_SERVICE_ROLE_KEY) {
    return res.status(500).end(sayfa('Şu an doğrulanamıyor', 'Kısa süre sonra tekrar deneyin.', '⚠️'));
  }

  const raw = typeof req.query?.token === 'string' ? req.query.token : '';
  // Uzunluk siniri: hash hesabina devasa govde sokulmasin.
  if (!raw || raw.length > 200) {
    return res.status(400).end(sayfa('Bağlantı geçersiz', 'Doğrulama bağlantısı eksik ya da bozuk görünüyor.', '⚠️'));
  }

  const hash = crypto.createHash('sha256').update(raw).digest('hex');

  try {
    // Son-kullanma DB tarafinda suzulur (sunucu saatine guvenme).
    const simdi = new Date().toISOString();
    const q = await sb(
      `creator_applications?verify_token_hash=eq.${encodeURIComponent(hash)}` +
      `&verify_expires_at=gt.${encodeURIComponent(simdi)}&select=id,handle,email_verified&limit=1`
    );
    const rows = q.ok ? await q.json().catch(() => []) : [];
    if (!rows.length) {
      return res.status(400).end(sayfa(
        'Bağlantı geçersiz ya da süresi dolmuş',
        'Doğrulama bağlantıları 24 saat geçerlidir. Formu yeniden gönderirseniz yeni bir bağlantı yollarız.', '⏳'));
    }

    const app = rows[0];
    if (app.email_verified) {
      return res.status(200).end(sayfa('Zaten doğrulanmış', 'Bu başvurunun e-postası daha önce doğrulanmıştı. Başka bir şey yapmanız gerekmiyor.', '✓'));
    }

    // Tek kullanimlik: token temizlenir ki link yeniden oynatilamasin.
    const up = await sb(`creator_applications?id=eq.${app.id}`, {
      method: 'PATCH',
      headers: { Prefer: 'return=minimal' },
      body: JSON.stringify({
        email_verified: true,
        verify_token_hash: null,
        verify_expires_at: null,
        updated_at: new Date().toISOString(),
      }),
    });
    if (!up.ok) {
      const txt = await up.text().catch(() => '');
      console.error('creator-verify patch', up.status, txt.slice(0, 200));
      return res.status(500).end(sayfa('Şu an doğrulanamıyor', 'Kısa süre sonra tekrar deneyin.', '⚠️'));
    }

    return res.status(200).end(sayfa(
      'E-postanız doğrulandı',
      'Teşekkürler — başvurunuz incelemeye alındı. Sonucu bu adrese yazacağız.', '✅'));
  } catch (e) {
    console.error('creator-verify', e);
    return res.status(500).end(sayfa('Şu an doğrulanamıyor', 'Kısa süre sonra tekrar deneyin.', '⚠️'));
  }
}
