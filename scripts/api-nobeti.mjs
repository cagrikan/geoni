// api/ altindaki Vercel fonksiyonlari icin nobet.
//
// UC SEYI KILITLER:
//  1. Her fonksiyon modulu YUKLENIYOR mu. `api/` icinde goreli import'ta `.js`
//     uzantisi unutulursa uretimde modul cozulmez ve uc 500 doner — bu ancak
//     canlida gorulur, kod incelemesinde gozden kacar.
//  2. Ziyaretciye is yapan uclarda ust akis cagrisinda ZAMAN ASIMI var mi.
//     try/catch bir HATAYI yakalar, TAKILMAYI yakalamaz: api.geoni.ai yavaslarsa
//     fonksiyon cevap veremeden kesilir, ziyaretci gecit hatasi gorur. Rozet
//     gomen musteri sitesinde bu KIRIK RESIM demektir.
//  3. Zaman asiminin GERCEKTEN calistigi — hic cevap vermeyen bir sunucuya
//     karsi olculur, iddia edilmez.
//
// Calistirma: scripts/api-nobeti.sh (gecici dizine kopyalar; depoya
// package.json eklemek Vercel'in derleme davranisini degistirebilirdi).
import http from 'node:http';
import { readFileSync } from 'node:fs';
import { fetchZamanAsimli } from './api/_lib/istek.js';

let kotu = 0;
const de = (ad, kosul, ek = '') => {
  console.log(`${kosul ? 'GECTI' : 'KALDI'}  ${ad}${ek ? '  — ' + ek : ''}`);
  if (!kosul) kotu = 1;
};

/** Ziyaretciye dogrudan is yapan uclar: takilirsa kullanici gorur. */
const ZIYARETCI_UCLARI = [
  './api/badge/[id].js',
  './api/s/[id].js',
  './api/ai-friendly.js',
  './api/r/[slug].js',
  // Marka tanima aracinin ucu: uc modeli SIRAYLA deniyor, beklemeler ust uste
  // biner -> zaman asimi olmadan fonksiyon tavani asilir.
  './api/lookup.js',
  // Form uclari: yanit e-posta gonderimine bagli, gonderim takilirsa form
  // "gonderiliyor"da kalir.
  // NOT: './api/audit-email.js' 2026-08-12'de SILINDI. Tek cagirani ana
  // sayfadaki "AI asistani" paneliydi; o panel hic acilmiyordu (acilis dugmesi
  // DOM'da yoktu) ve acilsa bile iki cevaptan uydurma "GEO ACILIYET SKORU"
  // uretiyordu. Panel kaldirilinca uc yetim kaldi — kimliksiz, KVKK
  // aydinlatmasiz bir mail ucu olarak saf saldiri yuzeyiydi.
  './api/creator-apply.js',
];

for (const yol of ZIYARETCI_UCLARI) {
  try {
    const m = await import(yol);
    de(`modul yukleniyor: ${yol}`, typeof m.default === 'function');
  } catch (e) {
    de(`modul yukleniyor: ${yol}`, false, e.message);
  }

  // Kaynakta ciplak `await fetch(` kalmasin: zaman asimsiz her cagri
  // fonksiyonu takilmaya acik birakir.
  const kaynak = readFileSync(new URL(yol, import.meta.url), 'utf8');
  const ciplak = kaynak.includes('await fetch(');
  de(`zaman asimsiz fetch YOK: ${yol}`, !ciplak, ciplak ? 'ciplak `await fetch(` var' : '');
}

// Hic cevap vermeyen sunucu — zaman asimi olmasaydi burada sonsuza kadar beklenirdi.
const takilan = http.createServer(() => {});
await new Promise((c) => takilan.listen(0, c));
const t0 = Date.now();
let hata = null;
try {
  await fetchZamanAsimli(`http://127.0.0.1:${takilan.address().port}/`, {}, 800);
} catch (e) { hata = e; }
const gecen = Date.now() - t0;
de('takilan ust akis KESILIYOR', hata !== null, hata?.name);
de('kesme suresi sinira yakin (<1500ms)', gecen < 1500, `${gecen}ms`);

// Saglam ust akista davranis DEGISMEMELI.
const saglam = http.createServer((_q, y) => y.end('{"ok":true}'));
await new Promise((c) => saglam.listen(0, c));
const r = await fetchZamanAsimli(`http://127.0.0.1:${saglam.address().port}/`, {}, 3000);
de('saglam ust akis normal donuyor', r.ok && (await r.json()).ok === true);

takilan.close();
saglam.close();
process.exit(kotu);
