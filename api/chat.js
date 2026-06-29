export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') return res.status(200).end();
  if (req.method !== 'POST') return res.status(405).json({ error: 'Method not allowed' });

  const { name, topic } = req.body;
  if (!name || !topic) return res.status(400).json({ error: 'Name and topic required' });

  if (!process.env.ANTHROPIC_API_KEY) {
    return res.status(500).json({ error: 'API key not configured' });
  }

  const query = `"${topic}" konusunda Türkiye'de öne çıkan isimler, kurumlar veya kaynaklar hangileridir? Gerçek, somut isimler ver.`;

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
        system: `Sen bir yapay zeka asistanısın. Kullanıcı belirli bir konu hakkında sana sorduğunda, o konuda Türkiye'de öne çıkan gerçek kişileri, kurumları, markaları veya kaynakları listele. Soyut kategori veya genel açıklama yazma — mutlaka somut, tanınmış isimler ver. Her satırda "• [İsim] — [bu konudaki rolü veya neden öne çıktığı]" formatında yaz. 4-6 isim sun. Markdown kullanma. Türkçe yanıtla.`,
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
