export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { data } = req.body;
  if (!data) return res.status(400).json({ error: 'Data required' });

  // Resend ile mail gönder
  try {
    const html = `
      <div style="font-family:Inter,sans-serif;max-width:600px;background:#07070F;color:#F1F5F9;padding:32px;border-radius:12px">
        <h2 style="color:#818CF8;margin-bottom:24px">🎯 Yeni GEO Audit Talebi</h2>
        <table style="width:100%;border-collapse:collapse">
          <tr><td style="padding:8px 0;color:#94A3B8;width:140px">Ad Soyad</td><td style="padding:8px 0;color:#F1F5F9"><b>${data.name}</b></td></tr>
          <tr><td style="padding:8px 0;color:#94A3B8">Şirket</td><td style="padding:8px 0;color:#F1F5F9"><b>${data.company}</b></td></tr>
          <tr><td style="padding:8px 0;color:#94A3B8">AI Durumu</td><td style="padding:8px 0;color:${data.companyKnown ? '#4ade80' : '#f87171'}">${data.companyKnown ? '✓ AI bilgi tabanında var' : '✗ AI bilgi tabanında yok'}</td></tr>
          <tr><td style="padding:8px 0;color:#94A3B8">Faaliyet Alanı</td><td style="padding:8px 0;color:#F1F5F9">${data.topic}</td></tr>
          <tr><td style="padding:8px 0;color:#94A3B8">Rakipler</td><td style="padding:8px 0;color:#F1F5F9">${data.competitors}</td></tr>
          <tr><td style="padding:8px 0;color:#94A3B8">Hedefler</td><td style="padding:8px 0;color:#F1F5F9">${data.goals}</td></tr>
          <tr><td style="padding:8px 0;color:#94A3B8">Mevcut Pazarlama</td><td style="padding:8px 0;color:#F1F5F9">${data.marketing}</td></tr>
          <tr><td style="padding:8px 0;color:#94A3B8">E-posta</td><td style="padding:8px 0;color:#22D3EE"><a href="mailto:${data.email}" style="color:#22D3EE">${data.email}</a></td></tr>
        </table>
        <div style="margin-top:24px;padding:16px;background:#0E0E1C;border-radius:8px;border-left:3px solid #818CF8">
          <p style="color:#94A3B8;font-size:13px;margin:0">Mini Rapor</p>
          <p style="color:#F1F5F9;margin-top:8px;line-height:1.6">${data.miniReport}</p>
        </div>
        <p style="color:#334155;font-size:11px;margin-top:24px">geoni.ai · ${new Date().toLocaleString('tr-TR')}</p>
      </div>
    `;

    const r = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${process.env.RESEND_API_KEY}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        from: 'GEONI <mail@geoni.ai>',
        to: ['mail@cagricakir.com.tr'],
        subject: `🎯 Yeni Audit Talebi — ${data.company}`,
        html
      })
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
