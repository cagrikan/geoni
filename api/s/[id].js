// Viral paylasim sayfasi: geoni.ai/s/<jobId>
// Sunucuda render edilir ki LinkedIn/X/WhatsApp on-izleme botlari OG
// etiketlerini gorebilsin (SPA olsa gormezlerdi). Skor karti + tarama CTA'si.
const API = 'https://api.geoni.ai';

function esc(s) {
  return String(s || '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

export default async function handler(req, res) {
  const { id } = req.query;
  if (!id || !/^[0-9a-f-]{10,40}$/i.test(id)) {
    res.writeHead(302, { Location: 'https://geoni.ai' });
    return res.end();
  }

  let data = null;
  try {
    const r = await fetch(`${API}/api/share/${encodeURIComponent(id)}`);
    if (r.ok) data = await r.json();
  } catch { /* API erisilemezse ana sayfaya duser */ }

  if (!data || typeof data.score !== 'number') {
    res.writeHead(302, { Location: 'https://geoni.ai' });
    return res.end();
  }

  const label = esc(data.label || '');
  const score = Math.round(data.score);
  const isWeb = data.type === 'web';
  const title = `AI Görünürlük Skoru: ${score}/100 — ${label}`;
  const desc = 'Senin skorun kaç? Markanın, adının veya sitenin ChatGPT, Gemini ve Perplexity\'deki görünürlüğünü 60 saniyede ücretsiz ölç.';
  const scoreColor = score >= 70 ? '#2fbd84' : score >= 40 ? '#F5A623' : '#f0616d';

  const html = `<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>${title}</title>
<meta name="description" content="${desc}">
<meta property="og:type" content="website">
<meta property="og:title" content="${title}">
<meta property="og:description" content="${desc}">
<meta property="og:image" content="https://geoni.ai/og-share.png">
<meta property="og:url" content="https://geoni.ai/s/${esc(id)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="${title}">
<meta name="twitter:description" content="${desc}">
<meta name="twitter:image" content="https://geoni.ai/og-share.png">
<link rel="icon" href="/favicon.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{min-height:100vh;display:grid;place-items:center;background:#0A0B10;color:#E7E9F2;font-family:'Inter',-apple-system,sans-serif;padding:24px}
.card{max-width:460px;width:100%;background:#10121A;border:1px solid rgba(124,134,245,.2);border-radius:18px;padding:40px 34px;text-align:center}
.logo{display:inline-flex;align-items:center;gap:9px;color:#E7E9F2;text-decoration:none;font-weight:800;font-size:1.05rem;margin-bottom:26px}
.logo img{border-radius:7px}
.label{font-size:1.05rem;font-weight:600;color:#A8ADC4;word-break:break-word}
.score{font-size:4.6rem;font-weight:800;letter-spacing:-.03em;line-height:1.1;color:${scoreColor};margin:6px 0 2px}
.of{color:#6E7391;font-size:.95rem;margin-bottom:18px}
.bar{height:8px;border-radius:99px;background:#1c1f2e;overflow:hidden;margin-bottom:26px}
.bar i{display:block;height:100%;width:${Math.max(score, 4)}%;background:${scoreColor};border-radius:99px}
.q{font-size:1.25rem;font-weight:700;margin-bottom:8px}
.sub{color:#A8ADC4;font-size:.92rem;line-height:1.6;margin-bottom:24px}
.cta{display:inline-block;background:#7C86F5;color:#fff;text-decoration:none;font-weight:700;font-size:1rem;padding:14px 34px;border-radius:10px}
.cta:hover{opacity:.9}
.free{color:#6E7391;font-size:.78rem;margin-top:12px}
</style>
</head>
<body>
<main class="card">
  <a class="logo" href="https://geoni.ai"><img src="/favicon.png" width="24" height="24" alt="">geoni</a>
  <div class="label">${label}${isWeb ? '' : ' · ' + (data.type === 'person' ? 'kişi' : 'marka')}</div>
  <div class="score">${score}</div>
  <div class="of">/ 100 · AI Görünürlük Skoru</div>
  <div class="bar"><i></i></div>
  <div class="q">AI seni tanıyor mu?</div>
  <p class="sub">Bu skor; ChatGPT, Gemini ve Perplexity'nin bu ${isWeb ? 'siteyi' : 'ismi'} ne kadar tanıdığını gösteriyor. Peki seninki kaç?</p>
  <a class="cta" href="https://app.geoni.ai?utm_source=share&utm_medium=scorecard">Kendi skorunu ölç →</a>
  <div class="free">60 saniye · ücretsiz · üyelik gerekmez</div>
</main>
</body>
</html>`;

  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  res.setHeader('Cache-Control', 's-maxage=3600, stale-while-revalidate=86400');
  res.statusCode = 200;
  return res.end(html);
}
