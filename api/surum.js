// Magazadaki guncel surum — mobil uygulamanin guncelleme uyarisi icin.
//
// NEDEN KENDI UCUMUZ, Apple'in lookup ucu DEGIL:
//   1. Play'in halka acik surum API'si YOK. `itunes.apple.com/lookup` yalniz
//      iOS'u cozer, Android guncellemesiz kalir.
//   2. Lookup ULKEYE GORE degisir ve saatlerce onbelleklidir.
//   3. En onemlisi: oradan "bu surumde veri kaybi var, MUTLAKA guncelle"
//      diyemezsiniz. `enAz` alani tam bunun icin var.
//
// Hicbir sey ALMAZ (govde/parametre okumaz), hicbir sey YAZMAZ. Kenarda
// onbelleklenir; yuk yaratmaz.
//
// ⚠️ ISLETIM: `surum` alani magazada YAYINLANDIGINDA guncellenir, incelemeye
// gonderildiginde DEGIL. Erken guncellenirse kullaniciya indiremeyecegi bir
// surum gosterilir.
//
// `enAz` yalnizca o surumun altinda kalmak VERI KAYBETTIRIYOR ya da uygulamayi
// KIRIYORSA yukseltilir. Zorunlu uyarinin "Sonra" secenegi yoktur; her yamada
// yukseltmek uyariyi degersizlestirir.

const SURUMLER = {
  // iOS 1.0.8: 12 Agustos 2026'da READY_FOR_SALE (build 80) — iTunes lookup
  // ve ASC ile 14 Agustos'ta dogrulandi. 1.0.7'de kalan uc gun boyunca bu alan
  // eskimisti: guncelleme uyarisi 1.0.8'i hic duyurmadi.
  ios: { surum: '1.0.8', enAz: '1.0.0' },
  // Android 20 Agustos 2026'da URETIM kanalinda yayinlandi (1.0.8 / vc12,
  // 176 ulke). Kapali test ayrica vc13'te (R8 surumu, testcilerde deneniyor).
  // Uretim acilana kadar enAz dusuk tutuluyor — kimseyi kilitlemeyelim.
  android: { surum: '1.0.8', enAz: '1.0.0' },
};

// 🪤 KENDINI KILITLEME KAPANI: `enAz > surum` olursa HERKESE zorunlu
// guncelleme gosterilir ve kimse indiremez. Uc, boyle bir yapilandirmayla
// ACILMAMALI — istemci tarafinda da ikinci savunma var (surum-karari.ts).
function sayisal(s) {
  return String(s || '').split('.').map((x) => parseInt(x, 10) || 0);
}
function karsilastir(a, b) {
  const A = sayisal(a);
  const B = sayisal(b);
  for (let i = 0; i < Math.max(A.length, B.length); i++) {
    const x = A[i] || 0;
    const y = B[i] || 0;
    if (x !== y) return x < y ? -1 : 1;
  }
  return 0;
}

export default function handler(req, res) {
  if (req.method !== 'GET') {
    res.setHeader('Allow', 'GET');
    return res.status(405).json({ error: 'method' });
  }

  // Bozuk yapilandirmayi ISTEMCIYE GONDERME: enAz > surum ise o platformu
  // gormezden gel. Sessiz kalmak, kullaniciyi kilitlemekten iyidir.
  const cikti = {};
  for (const [platform, bilgi] of Object.entries(SURUMLER)) {
    if (bilgi.enAz && karsilastir(bilgi.enAz, bilgi.surum) > 0) continue;
    cikti[platform] = { surum: bilgi.surum, enAz: bilgi.enAz };
  }

  // Kenarda 10 dk: surum gunde birkac kez degisir, saniyelik tazelik gereksiz.
  res.setHeader('Cache-Control', 's-maxage=600, stale-while-revalidate=3600');
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  return res.status(200).json(cikti);
}

// Test icin disari acilir (uc davranisi + kilitleme kapani sinanabilsin).
export const _test = { SURUMLER, karsilastir };
