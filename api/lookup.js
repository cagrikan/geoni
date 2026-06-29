export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { name } = req.body;
  if (!name) return res.status(400).json({ error: 'Name required' });

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
        max_tokens: 300,
        system: `Kullanıcı bir şirket, kurum veya kişi adı verir. Bunu tanıyıp tanımadığını JSON formatında belirt. Sadece JSON döndür, başka hiçbir şey yazma:
{"known": true/false, "name": "tam isim", "description": "bir cümle açıklama (biliniyorsa, yoksa null)", "website": "website URL (biliniyorsa, yoksa null)", "sector": "sektör (biliniyorsa, yoksa null)"}`,
        messages: [{ role: 'user', content: `"${name}" hakkında ne biliyorsun? JSON formatında ver.` }]
      })
    });

    const data = await response.json();
    const text = data.content[0].text.replace(/```json|```/g, '').trim();
    try {
      res.status(200).json(JSON.parse(text));
    } catch {
      res.status(200).json({ known: false, name });
    }
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
}
