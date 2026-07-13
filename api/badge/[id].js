// Gomulebilir skor rozeti: geoni.ai/badge/<jobId>  (SVG)
// Kullanici sitesine koyar: <a href="https://geoni.ai/s/ID"><img src="https://geoni.ai/badge/ID"></a>
// Ona statü gostergesi, bize her musteri sitesinden kalici backlink + marka izi.
const API = 'https://api.geoni.ai';

function esc(s) {
  return String(s || '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

export default async function handler(req, res) {
  let { id } = req.query;
  id = String(id || '').replace(/\.svg$/i, '');
  if (!/^[0-9a-f-]{10,40}$/i.test(id)) {
    res.statusCode = 404;
    return res.end('not found');
  }

  let data = null;
  try {
    const r = await fetch(`${API}/api/share/${encodeURIComponent(id)}`);
    if (r.ok) data = await r.json();
  } catch { /* asagida 404 */ }

  if (!data || typeof data.score !== 'number') {
    res.statusCode = 404;
    return res.end('not found');
  }

  const score = Math.round(data.score);
  // Rozet yalnizca "yesil" (70+) skorlara verilir — ve uzerinde PUAN YOK:
  // skor tablosu degil guven muhru ("AI bizi goruyor / checked by GEONI").
  if (score < 70) {
    res.statusCode = 404;
    return res.end('badge available for scores 70+');
  }
  const color = '#2fbd84';
  const label = esc((data.label || '').slice(0, 30));
  const lang = String(req.query.lang || 'tr').toLowerCase() === 'en' ? 'en' : 'tr';
  const eyebrow = lang === 'en' ? 'CHECKED BY GEONI' : 'AI B\u0130Z\u0130 G\u00d6R\u00dcYOR';

  // Sabit genislikli, koyu marka temali "shield" rozet
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="232" height="44" role="img" aria-label="AI-visible — checked by GEONI">
  <title>${label} — ${lang === 'en' ? 'AI-visible, checked by GEONI' : 'AI bizi görüyor — GEONI doğruladı'}</title>
  <defs>
    <clipPath id="r"><rect width="232" height="44" rx="9"/></clipPath>
  </defs>
  <g clip-path="url(#r)">
    <rect width="232" height="44" fill="#0A0B10"/>
    <rect x="160" width="72" height="44" fill="#10121A"/>
    <rect width="232" height="44" rx="9" fill="none" stroke="rgba(124,134,245,.45)" stroke-width="1.5"/>
    <!-- G mark: yildiz kumesi -->
    <g transform="translate(12,10)">
      <path d="M17 4 A9.5 9.5 0 1 0 17 20" fill="none" stroke="#7C86F5" stroke-width="2.6" stroke-linecap="round"/>
      <circle cx="20" cy="7" r="1.6" fill="#7C86F5"/>
      <circle cx="21.5" cy="15" r="2" fill="#F5A623"/>
      <line x1="20" y1="7" x2="21.5" y2="15" stroke="#7C86F5" stroke-width="1" opacity=".6"/>
    </g>
    <text x="46" y="18" font-family="-apple-system,Segoe UI,Helvetica,Arial,sans-serif" font-size="10.5" fill="#A8ADC4" letter-spacing=".4">${eyebrow}</text>
    <text x="46" y="33" font-family="-apple-system,Segoe UI,Helvetica,Arial,sans-serif" font-size="11" font-weight="600" fill="#E7E9F2">geoni.ai</text>
    <path d="M188 22.5 l5.5 5.5 l11 -12" fill="none" stroke="${color}" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"/>
  </g>
</svg>`;

  res.setHeader('Content-Type', 'image/svg+xml; charset=utf-8');
  res.setHeader('Cache-Control', 's-maxage=86400, stale-while-revalidate=604800');
  res.statusCode = 200;
  return res.end(svg);
}
