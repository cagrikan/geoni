export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') return res.status(200).end();
  if (req.method !== 'POST') return res.status(405).json({ error: 'Method not allowed' });

  const { sector, company } = req.body;
  if (!sector || !company) return res.status(400).json({ error: 'Sector and company required' });

  if (!process.env.ANTHROPIC_API_KEY) {
    return res.status(500).json({ error: 'API key not configured' });
  }

  const query = `Türkiye'de ${sector} sektöründe faaliyet gösteren gerçek şirket isimlerini listele. Sadece somut firma adları ver.`;

  try {
    const response = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': process.env.ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01'
      },
      body: JSON.stringify({
        model: 'claude-haiku-4-5-20251001',
        max_tokens: 500,
        system: "Sen yardımcı bir yapay zeka asistanısın. Türkiye'de belirli bir sektörde faaliyet gösteren GERÇEK firma ve şirket isimlerini listele. Mutlaka somut, bilinen şirket isimleri ver — kategori veya genel tanım yazma. Her satırda '• [Şirket Adı] — [ne yaptığı]' formatında yaz. 4-5 gerçek firma ismi sun. Markdown kullanma. Türkçe yanıtla.",
        messages: [{ role: 'user', content: query }]
      })
    });

    const data = await response.json();

    if (!response.ok) {
      return res.status(response.status).json({ error: data.error?.message || 'Anthropic API error' });
    }

    res.status(200).json(data);
  } catch (error) {
    res.status(500).json({ error: error.message || 'Server error' });
  }
}
