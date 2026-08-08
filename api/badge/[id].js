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
  // 🪤 OKUNAMAYAN SITEYE MUHUR VERILMEZ (2026-08-08'de olculdu).
  // Rozet bizim kamuya acik guvenilirlik isaretimiz. Eskiden YALNIZ skora
  // bakiliyordu: botlari engelleyen, sitesinden TEK SAYFA bile okuyamadigimiz
  // bir alan 80 alip muhuru gomebiliyordu. Son 30 gunde 70+ alan 19 taramanin
  // 5'i (%26,3) sifir sayfaydi. Lig ayni butunluk kuralini zaten uyguluyordu
  // (MIN_CRAWLED_PAGES = 3, db.py); rozet uygulamiyordu — tek kural, iki farkli
  // yer. `pages` alani share payload'ina bu yuzden eklendi.
  // Geriye donuk: alan YOKSA (eski/onbellekli yanit) engellemeyiz — mevcut
  // rozetler kirilmasin; yalniz KESIN olarak yetersiz oldugunda reddedilir.
  const MIN_PAGES = 3;
  if (typeof data.pages === 'number' && data.pages < MIN_PAGES) {
    res.statusCode = 404;
    return res.end('badge requires a readable site (we could not crawl it)');
  }
  const color = '#2fbd84';
  const label = esc((data.label || '').slice(0, 30));

  // Sabit genislikli, koyu marka temali "shield" rozet
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="232" height="44" role="img" aria-label="AI Friendly — checked by GEONI">
  <title>${label} — AI Friendly · Checked by GEONI</title>
  <defs>
    <clipPath id="r"><rect width="232" height="44" rx="9"/></clipPath>
  </defs>
  <g clip-path="url(#r)">
    <rect width="232" height="44" fill="#0A0B10"/>
    <rect x="160" width="72" height="44" fill="#10121A"/>
    <rect width="232" height="44" rx="9" fill="none" stroke="rgba(124,134,245,.45)" stroke-width="1.5"/>
    <!-- Gercek GEONI logosu (sitedeki vektorle birebir; 80x80 -> 26px olcek) -->
    <g transform="translate(10,9) scale(0.325)">
      <line x1="38" y1="12" x2="25" y2="22" stroke="#7C86F5" stroke-width="2" opacity=".4"/>
      <line x1="10" y1="40" x2="20" y2="25" stroke="#7C86F5" stroke-width="2" opacity=".4"/>
      <line x1="10" y1="40" x2="18" y2="56" stroke="#7C86F5" stroke-width="2" opacity=".4"/>
      <line x1="38" y1="68" x2="25" y2="58" stroke="#7C86F5" stroke-width="2" opacity=".5"/>
      <line x1="60" y1="40" x2="70" y2="24" stroke="#7C86F5" stroke-width="2" opacity=".5"/>
      <line x1="38" y1="12" x2="52" y2="8"  stroke="#7C86F5" stroke-width="2" opacity=".5"/>
      <line x1="52" y1="8"  x2="70" y2="24" stroke="#7C86F5" stroke-width="2" opacity=".5"/>
      <path d="M 38 12 A 30 30 0 1 0 38 68" fill="none" stroke="#7C86F5" stroke-width="6.5" stroke-linecap="round"/>
      <path d="M 50 40 L 62 40" fill="none" stroke="#7C86F5" stroke-width="6.5" stroke-linecap="round"/>
      <path d="M 62 40 L 62 58" fill="none" stroke="#7C86F5" stroke-width="6.5" stroke-linecap="round"/>
      <circle cx="38" cy="12" r="3" fill="#7C86F5"/>
      <circle cx="10" cy="40" r="2.5" fill="#7C86F5"/>
      <circle cx="38" cy="68" r="3" fill="#7C86F5"/>
      <circle cx="62" cy="40" r="4.2" fill="#F5A623"/>
      <circle cx="62" cy="40" r="2.2" fill="#0A0B10"/>
      <circle cx="62" cy="40" r="1.1" fill="#F5A623"/>
      <circle cx="50" cy="40" r="2.5" fill="#7C86F5"/>
      <circle cx="62" cy="58" r="2.5" fill="#7C86F5"/>
      <circle cx="70" cy="24" r="2.5" fill="#7C86F5"/>
      <circle cx="52" cy="8"  r="2.2" fill="#7C86F5"/>
    </g>
    <text x="46" y="18" font-family="-apple-system,Segoe UI,Helvetica,Arial,sans-serif" font-size="10.5" fill="#A8ADC4" letter-spacing=".6">AI FRIENDLY</text>
    <text x="46" y="33" font-family="-apple-system,Segoe UI,Helvetica,Arial,sans-serif" font-size="11" font-weight="700" fill="#E7E9F2">Checked by GEONI</text>
    <path d="M188 22.5 l5.5 5.5 l11 -12" fill="none" stroke="${color}" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"/>
  </g>
</svg>`;

  res.setHeader('Content-Type', 'image/svg+xml; charset=utf-8');
  res.setHeader('Cache-Control', 's-maxage=86400, stale-while-revalidate=604800');
  res.statusCode = 200;
  return res.end(svg);
}
