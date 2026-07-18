const ALLOWED_ORIGINS = new Set([
  'https://geoni.ai',
  'https://www.geoni.ai',
]);

// HTML-escape: alan değerleri e-posta gövdesine ham gömülüyordu (XSS/HTML
// enjeksiyonu — kurucunun gelen kutusuna sahte içerik). Tüm dinamik değerler
// buradan geçer.
function esc(v) {
  return String(v == null ? '' : v)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
    .slice(0, 2000); // alan başına üst sınır (kota/gövde şişmesi koruması)
}

function isValidEmail(e) {
  return typeof e === 'string' && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(e) && e.length <= 254;
}

export default async function handler(req, res) {
  const origin = req.headers.origin;
  if (ALLOWED_ORIGINS.has(origin)) {
    res.setHeader('Access-Control-Allow-Origin', origin);
  }
  res.setHeader('Vary', 'Origin');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();
  if (req.method !== 'POST') return res.status(405).json({ error: 'Method not allowed' });

  const { data } = req.body || {};
  if (!data || typeof data !== 'object') return res.status(400).json({ error: 'Data required' });

  const replyTo = isValidEmail(data.email) ? data.email : undefined;

  try {
    const html = `
      <div style="font-family:Inter,sans-serif;max-width:600px;background:#07070F;color:#F1F5F9;padding:32px;border-radius:12px">
        <h2 style="color:#818CF8;margin-bottom:24px">Yeni GEO Audit Talebi</h2>
        <table style="width:100%;border-collapse:collapse">
          <tr><td style="padding:8px 0;color:#94A3B8;width:140px">Ad Soyad</td><td style="padding:8px 0;color:#F1F5F9"><b>${esc(data.name)}</b></td></tr>
          <tr><td style="padding:8px 0;color:#94A3B8">Şirket</td><td style="padding:8px 0;color:#F1F5F9"><b>${esc(data.company)}</b></td></tr>
          <tr><td style="padding:8px 0;color:#94A3B8">AI Durumu</td><td style="padding:8px 0;color:${data.companyKnown ? '#4ade80' : '#f87171'}">${data.companyKnown ? '✓ AI bilgi tabanında var' : '✗ AI bilgi tabanında yok'}</td></tr>
          <tr><td style="padding:8px 0;color:#94A3B8">Faaliyet Alanı</td><td style="padding:8px 0;color:#F1F5F9">${esc(data.topic)}</td></tr>
          <tr><td style="padding:8px 0;color:#94A3B8">Rakipler</td><td style="padding:8px 0;color:#F1F5F9">${esc(data.competitors)}</td></tr>
          <tr><td style="padding:8px 0;color:#94A3B8">Hedefler</td><td style="padding:8px 0;color:#F1F5F9">${esc(data.goals)}</td></tr>
          <tr><td style="padding:8px 0;color:#94A3B8">Mevcut Pazarlama</td><td style="padding:8px 0;color:#F1F5F9">${esc(data.marketing)}</td></tr>
          <tr><td style="padding:8px 0;color:#94A3B8">E-posta</td><td style="padding:8px 0;color:#22D3EE">${replyTo ? `<a href="mailto:${esc(replyTo)}" style="color:#22D3EE">${esc(replyTo)}</a>` : '<span style="color:#94A3B8">(geçersiz)</span>'}</td></tr>
        </table>
        <div style="margin-top:24px;padding:16px;background:#0E0E1C;border-radius:8px;border-left:3px solid #818CF8">
          <p style="color:#94A3B8;font-size:13px;margin:0">Mini Rapor</p>
          <p style="color:#F1F5F9;margin-top:8px;line-height:1.6">${esc(data.miniReport)}</p>
        </div>
        <p style="color:#334155;font-size:11px;margin-top:24px">geoni.ai · ${new Date().toLocaleString('tr-TR')}</p>
      </div>
    `;

    const body = {
      from: 'GEONI <mail@geoni.ai>',
      to: ['mail@cagricakir.com.tr'],
      subject: `Yeni Audit Talebi: ${esc(data.company)}`.slice(0, 200),
      html,
    };
    if (replyTo) body.reply_to = replyTo;

    const r = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${process.env.RESEND_API_KEY}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(body)
    });

    if (r.ok) {
      res.status(200).json({ success: true });
    } else {
      const err = await r.json();
      res.status(500).json({ error: err.message || 'Email failed' });
    }
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
}
