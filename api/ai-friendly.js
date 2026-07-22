// AI Friendly Ligi: geoni.ai/ai-friendly
// En yuksek skorlu sitelerin herkese acik siralamasi; muhur (✓) yalnizca
// 70+ satirlarda. Devlerin de barajin altinda kaldigi gorunsun diye liste
// 70 sartina bagli degil — bu, muhru daha da degerli kilar. Kisi/marka
// taramalari listelenmez (yalnizca siteler).
const API = 'https://api.geoni.ai';

function esc(s) {
  return String(s || '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

export default async function handler(req, res) {
  let items = [];
  try {
    const r = await fetch(`${API}/api/ai-friendly`);
    if (r.ok) items = (await r.json()).items || [];
  } catch { /* bos liste ile render */ }

  const q = String(req.query.lang || '').toLowerCase();
  const accept = String(req.headers['accept-language'] || '').toLowerCase();
  const lang = q === 'tr' || q === 'en' ? q : (accept.startsWith('tr') || accept.includes(',tr') ? 'tr' : 'en');
  const L = lang === 'tr' ? {
    title: 'AI Friendly Ligi — GEONI',
    desc: 'AI motorlarının en iyi tanıdığı siteler: 70+ AI Görünürlük Skoru yapan herkes ligde. Sen de mührü kazan.',
    h1: 'AI Friendly Ligi',
    sub: 'ChatGPT, Gemini ve Perplexity\'nin en iyi tanıdığı siteler. 70+ skor yapan herkes "AI Friendly · Checked by GEONI" mührünü kullanmaya hak kazanır ve bu listede yerini alır.',
    col_site: 'Site', col_score: 'Skor', col_seal: 'Mühür', col_date: 'Tarih',
    empty: 'Lig yeni açıldı — ilk sıra seni bekliyor.',
    cta_h: 'Sen de mührü kazan',
    cta_p: 'Sitenin AI görünürlüğünü 60 saniyede ücretsiz ölç. 70\'i geçersen mühür ve ligdeki yerin hazır.',
    cta_btn: 'Ücretsiz Tara →',
    optout: 'Skorlar, herkese açık web verisi üzerinden yapılan taramalarla ölçülür. Sitenizin bu listede yer almasını istemiyorsanız yazmanız yeterli:',
    optout_link: 'listeden kaldırılma talebi gönder',
    optout_subject: 'AI Friendly Ligi - Listeden kaldirma talebi',
  } : {
    title: 'AI Friendly Leaderboard — GEONI',
    desc: 'The sites AI engines know best: everyone scoring 70+ on AI Visibility makes the board. Earn your seal.',
    h1: 'AI Friendly Leaderboard',
    sub: 'The sites ChatGPT, Gemini and Perplexity know best. Score 70+ and you earn the right to use the "AI Friendly · Checked by GEONI" seal — and your place on this board.',
    col_site: 'Site', col_score: 'Score', col_seal: 'Seal', col_date: 'Date',
    empty: 'The board just opened — first place is waiting for you.',
    cta_h: 'Earn your seal',
    cta_p: 'Measure your site\'s AI visibility free in 60 seconds. Cross 70 and the seal — and your spot here — is yours.',
    cta_btn: 'Scan Free →',
    optout: 'Scores are measured from publicly available web data. If you\'d rather not have your site listed here, just let us know:',
    optout_link: 'request removal from the board',
    optout_subject: 'AI Friendly Board - Removal request',
  };

  const rows = items.map((it, i) => {
    const seal = it.seal ?? (it.score >= 70); // eski API cevabinda seal alani yok
    const href = `/s/${esc(it.job_id)}`;
    // Site hucresi gercek <a> link: klavye + ekran okuyucu + SEO erisilebilir.
    // Satir tiklamasi yalnizca fare kolayligi (onclick JS kapaliysa link calisir).
    // onclick job_id'yi JS string'ine ENJEKTE ETMEZ; zaten escape'li <a> href'ini
    // okur -> JS-context injection yuzeyi yok (savunma-derinligi).
    return `
    <tr class="lb-row" onclick="location.href=this.querySelector('.site-link').href">
      <td class="rank">${i + 1}</td>
      <td class="site"><a class="site-link" href="${href}">${esc(it.domain)}</a></td>
      <td class="score${seal ? '' : ' score--low'}">${Math.round(it.score)}</td>
      <td class="sealcol">${seal ? '<span class="sealchip">✓</span>' : '<span class="nochip">—</span>'}</td>
      <td class="date">${esc(it.date)}</td>
    </tr>`;
  }).join('');

  const html = `<!DOCTYPE html>
<html lang="${lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${L.title}</title>
<meta name="description" content="${L.desc}">
<link rel="canonical" href="https://geoni.ai/ai-friendly">
<meta property="og:type" content="website">
<meta property="og:title" content="${L.h1} — GEONI">
<meta property="og:description" content="${L.desc}">
<meta property="og:image" content="https://geoni.ai/og-share.png">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="/favicon.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
<script src="/theme-toggle.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#F7F8FA;--surface:#FFFFFF;--surface-2:#F1F2F6;--text:#16181D;--text-sub:#4D5464;--text-muted:#8A90A0;--accent:#5B63E8;--border:rgba(91,99,232,.16);--border-soft:rgba(22,24,29,.08)}
:root[data-theme="dark"]{--bg:#0A0B10;--surface:#10121A;--surface-2:#161926;--text:#E7E9F2;--text-sub:#A8ADC4;--text-muted:#6E7391;--accent:#7C86F5;--border:rgba(124,134,245,.18);--border-soft:rgba(124,134,245,.08)}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0A0B10;--surface:#10121A;--surface-2:#161926;--text:#E7E9F2;--text-sub:#A8ADC4;--text-muted:#6E7391;--accent:#7C86F5;--border:rgba(124,134,245,.18);--border-soft:rgba(124,134,245,.08)}}
body{min-height:100vh;background:var(--bg);color:var(--text);font-family:'Inter',-apple-system,sans-serif;line-height:1.6;overflow-x:hidden;transition:background .2s,color .2s}
.tablewrap{overflow-x:auto;border-radius:14px}
.wrap{max-width:720px;margin:0 auto;padding:28px 20px 80px}
.top{display:flex;align-items:center;gap:10px;margin-bottom:34px}
.top a.brand{display:flex;align-items:center;gap:9px;color:var(--text);text-decoration:none;font-weight:800;font-size:1.05rem}
.top img{border-radius:7px}
.lang{margin-left:auto;font-size:.8rem;color:var(--text-muted)}
.lang a{color:var(--text-sub);text-decoration:none}
.lang b{color:var(--text)}
.titlerow{display:flex;align-items:center;gap:20px;flex-wrap:wrap;margin-bottom:10px}
.titlerow .seal{margin-left:auto}
.seal{display:inline-flex;align-items:center;gap:8px;border:1px solid rgba(47,189,132,.4);background:rgba(47,189,132,.08);color:#2fbd84;font-size:.72rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;border-radius:999px;padding:5px 13px;white-space:nowrap}
h1{font-size:clamp(1.8rem,5vw,2.5rem);font-weight:800;letter-spacing:-.02em}
.sub{color:var(--text-sub);font-size:.98rem;max-width:56ch;margin-bottom:32px}
table{width:100%;border-collapse:collapse;background:var(--surface);border:1px solid var(--border);border-radius:14px;overflow:hidden;font-size:.95rem}
thead th{font-family:'JetBrains Mono',monospace;font-size:.68rem;letter-spacing:.12em;text-transform:uppercase;color:var(--text-muted);text-align:left;padding:12px 16px;border-bottom:1px solid var(--border)}
tbody tr{cursor:pointer;transition:background .12s}
tbody tr:hover{background:var(--surface-2)}
td{padding:13px 16px;border-bottom:1px solid var(--border-soft)}
tbody tr:last-child td{border-bottom:0}
.rank{font-family:'JetBrains Mono',monospace;font-weight:700;color:var(--accent);width:44px}
tbody tr:nth-child(1) .rank{color:#F5A623}
.site{font-weight:600}
.site-link{color:inherit;text-decoration:none;outline:none}
.site-link:hover{text-decoration:underline}
.site-link:focus-visible{outline:2px solid var(--accent);outline-offset:3px;border-radius:3px}
.score{font-family:'JetBrains Mono',monospace;font-weight:700;color:#2fbd84;width:64px}
.score--low{color:#F5A623}
.sealcol{width:64px;text-align:center}
.sealchip{display:inline-flex;align-items:center;justify-content:center;width:24px;height:24px;border-radius:50%;border:1px solid rgba(47,189,132,.5);background:rgba(47,189,132,.12);color:#2fbd84;font-weight:800;font-size:.85rem}
.nochip{color:var(--text-muted);opacity:.5}
.date{color:var(--text-muted);font-size:.82rem;width:106px;white-space:nowrap}
.empty{padding:36px 16px;text-align:center;color:var(--text-muted)}
@media (max-width:480px){.date,thead th:nth-child(5){display:none}td{padding:11px 8px}thead th{padding:10px 8px}.rank{width:28px}.score,.sealcol{width:46px}table{font-size:.88rem}}
.cta{margin-top:36px;background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:30px 26px;text-align:center}
.cta h2{font-size:1.25rem;margin-bottom:6px}
.cta p{color:var(--text-sub);font-size:.9rem;max-width:48ch;margin:0 auto 20px}
.cta a{display:inline-block;background:var(--accent);color:#fff;text-decoration:none;font-weight:700;padding:12px 30px;border-radius:9px}
.cta a:hover{opacity:.9}
.optout{margin-top:22px;text-align:center;color:var(--text-muted);font-size:.78rem;max-width:62ch;margin-left:auto;margin-right:auto}
.optout a{color:var(--text-sub)}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <a class="brand" href="https://geoni.ai"><img src="/favicon.png" width="24" height="24" alt="">geoni</a>
    <div class="lang">${lang === 'tr' ? `<b>TR</b> · <a href="?lang=en">EN</a>` : `<a href="?lang=tr">TR</a> · <b>EN</b>`}</div>
  </div>
  <div class="titlerow">
    <h1>${L.h1}</h1>
    <div class="seal">✓ AI FRIENDLY · CHECKED BY GEONI</div>
  </div>
  <p class="sub">${L.sub}</p>
  <div class="tablewrap"><table>
    <thead><tr><th>#</th><th>${L.col_site}</th><th>${L.col_score}</th><th>${L.col_seal}</th><th>${L.col_date}</th></tr></thead>
    <tbody>${rows || `<tr><td colspan="5" class="empty">${L.empty}</td></tr>`}</tbody>
  </table></div>
  <div class="cta">
    <h2>${L.cta_h}</h2>
    <p>${L.cta_p}</p>
    <a href="https://app.geoni.ai?utm_source=leaderboard">${L.cta_btn}</a>
  </div>
  <p class="optout">${L.optout} <a href="mailto:mail@geoni.ai?subject=${encodeURIComponent(L.optout_subject)}">${L.optout_link}</a>.</p>
</div>
</body>
</html>`;

  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  res.setHeader('Cache-Control', 's-maxage=600, stale-while-revalidate=3600');
  res.setHeader('Vary', 'Accept-Language');
  res.statusCode = 200;
  return res.end(html);
}
