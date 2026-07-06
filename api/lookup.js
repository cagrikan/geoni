const SYSTEM_INSTRUCTION = `Kullanıcı bir şirket, kurum veya kişi adı verir. Bunu tanıyıp tanımadığını JSON formatında belirt. Sadece JSON döndür, başka hiçbir şey yazma:
{"known": true/false, "name": "tam isim", "description": "bir cümle açıklama (biliniyorsa, yoksa null)", "website": "website URL (biliniyorsa, yoksa null)", "sector": "sektör (biliniyorsa, yoksa null)"}`;

function parseJsonResult(text) {
  try {
    const cleaned = (text || '').replace(/```json|```/g, '').trim();
    return JSON.parse(cleaned);
  } catch {
    return null;
  }
}

async function askClaude(prompt) {
  if (!process.env.ANTHROPIC_API_KEY) return null;
  try {
    const r = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': process.env.ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01',
      },
      body: JSON.stringify({
        model: 'claude-haiku-4-5-20251001',
        max_tokens: 300,
        system: SYSTEM_INSTRUCTION,
        messages: [{ role: 'user', content: prompt }],
      }),
    });
    if (!r.ok) return null;
    const data = await r.json();
    return parseJsonResult(data.content?.[0]?.text);
  } catch {
    return null;
  }
}

async function askChatGPT(prompt) {
  if (!process.env.OPENAI_API_KEY) return null;
  try {
    const r = await fetch('https://api.openai.com/v1/chat/completions', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${process.env.OPENAI_API_KEY}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model: 'gpt-4o-mini',
        max_tokens: 300,
        response_format: { type: 'json_object' },
        messages: [
          { role: 'system', content: SYSTEM_INSTRUCTION },
          { role: 'user', content: prompt },
        ],
      }),
    });
    if (!r.ok) return null;
    const data = await r.json();
    return parseJsonResult(data.choices?.[0]?.message?.content);
  } catch {
    return null;
  }
}

async function askGemini(prompt) {
  if (!process.env.GOOGLE_API_KEY) return null;
  try {
    const r = await fetch('https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'x-goog-api-key': process.env.GOOGLE_API_KEY },
      body: JSON.stringify({
        contents: [{ role: 'user', parts: [{ text: `${SYSTEM_INSTRUCTION}\n\n${prompt}` }] }],
        generationConfig: { maxOutputTokens: 300, temperature: 0.3 },
      }),
    });
    if (!r.ok) return null;
    const data = await r.json();
    const text = (data.candidates?.[0]?.content?.parts || []).map((p) => p.text || '').join(' ');
    return parseJsonResult(text);
  } catch {
    return null;
  }
}

// Kademeli kontrol: once Claude'a sor, o "biliyorum" derse orada dur. O
// bilmiyorsa (ya da yanit alinamadiysa) ChatGPT'ye sor, o da bilmiyorsa
// Gemini'ye sor. Her asamada gercekten sorulan modeller "models" alaninda
// gorunur - sadece ilk taniyan modelde durup gereksiz API cagrisi/maliyeti
// yapilmaz.
const PROVIDERS = [
  ['claude', askClaude],
  ['chatgpt', askChatGPT],
  ['gemini', askGemini],
];

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { name } = req.body;
  if (!name) return res.status(400).json({ error: 'Name required' });

  const prompt = `"${name}" hakkında ne biliyorsun? JSON formatında ver.`;

  const models = {};
  let primary = null;

  for (const [key, askFn] of PROVIDERS) {
    const result = await askFn(prompt);
    if (!result) continue; // key yok ya da istek basarisiz - o modeli atla
    models[key] = { known: !!result.known };
    if (!primary || result.known) primary = result;
    if (result.known) break; // taniyan ilk model bulundu, kademeyi durdur
  }

  if (Object.keys(models).length === 0) {
    return res.status(500).json({ error: 'No AI model available to answer' });
  }

  const recognitionCount = Object.values(models).filter((m) => m.known).length;

  res.status(200).json({
    known: recognitionCount > 0,
    name: primary?.name || name,
    description: primary?.description || null,
    website: primary?.website || null,
    sector: primary?.sector || null,
    models,
    recognition_count: recognitionCount,
    total_models: Object.keys(models).length,
  });
}
