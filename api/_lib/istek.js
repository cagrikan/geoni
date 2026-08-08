// Zaman asimli fetch — Vercel fonksiyonlari icin.
//
// NEDEN: bu klasordeki fonksiyonlarin HICBIRINDE ust akis cagrisinda zaman
// asimi yoktu (2026-08-09'da tarandi: 17 `await fetch`, sifir AbortSignal).
// try/catch bir HATAYI yakalar, TAKILMAYI yakalamaz: api.geoni.ai yavaslarsa
// (or. olcek sifirdan kalkarken) fonksiyon cevap veremeden platform tarafindan
// kesilir ve ziyaretciye geciding 5xx'i doner. Rozet gomen musteri sitesinde
// bu KIRIK RESIM demek — bizim guvenilirlik muhrumuzun kirik gorunmesi.
//
// Cozum: her cagriya ust sinir koy. Sinir dolarsa cagiran normal "erisemedim"
// dalina duser (zaten hepsinde var) ve ziyaretci hizli, anlamli bir yanit alir.
//
// 🪤 `api/` icinde goreli import'ta `.js` uzantisi ZORUNLU — uzantisiz yazilirsa
// uretimde modul cozulmez ve fonksiyon 500 doner (daha once yasandi).

/** Varsayilan ust sinir: 6 sn. Vercel fonksiyon tavaninin altinda kalir. */
export const VARSAYILAN_MS = 6000;

/**
 * `fetch` gibi calisir, ama `ms` sonunda istegi iptal eder.
 * Iptal edildiginde AbortError firlatir — cagiran tarafta normal catch dali.
 */
export async function fetchZamanAsimli(url, opts = {}, ms = VARSAYILAN_MS) {
  // AbortSignal.timeout Node 18+'ta var; Vercel calisma zamani bunu karsiliyor.
  // Yine de yoksa elle AbortController'a duselim ki bir calisma zamani
  // farkinda sessizce zaman asimisiz kalmayalim.
  if (typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function') {
    return fetch(url, { ...opts, signal: AbortSignal.timeout(ms) });
  }
  const kontrol = new AbortController();
  const sayac = setTimeout(() => kontrol.abort(), ms);
  try {
    return await fetch(url, { ...opts, signal: kontrol.signal });
  } finally {
    clearTimeout(sayac);
  }
}
