"""
GEONI Scanner — Alintilanan kaynagin SAYFA TIPI (2026-08-02, GOLGE MOD).

NEDEN: GEONI bugune kadar "ChatGPT seni onermiyor" diyebiliyordu ama "peki ne
yapayim" sorusuna genel cevap veriyordu (sema ekle, bot izni ver). Sayfa tipi
olculursek cevap SOMUTLASIR:

    "Bu kategoride AI'in alintiladigi sayfalarin %53'u KARSILASTIRMA LISTESI.
     Senin bu tipte tek sayfan yok. Rakibinin var."

ILK OLCUM (2026-08-02, AI Overview'in alintiladigi 61 tekil sayfa, 4 tarama):
    liste/karsilastirma  %49
    hizmet/urun          %30
    siniflandirilamayan  %15
    rehber/aciklama      %5    <- GEONI'nin urettigi TEK tip
    sosyal/video         %2
Elle denetlendi: "diger" kutusundaki 9 sayfanin en az 3'u aslinda liste,
4'u hizmet sayfasi. Duzeltilse liste ~%53'e cikar. Yani hata payi bulguyu
GUCLENDIRIYOR. Bu dosyadaki kurallar o denetimin sonucudur.

GOLGE MOD: skoru DEGISTIRMEZ. n=61, 4 tarama, 2 alan adi, TEK kategori — yon
verir, kanun degil. Genellemek icin 20-30 sektor gerekir; AI Overview modulu
her taramada bu veriyi zaten topluyor, EK MALIYET YOK. 3-4 hafta sonra
"sinyal gercek mi" sorusu gercek veriyle cevaplanacak.

Agir bagimlilik YOK -> CI'nin minimal ortaminda test edilebilir.
"""

import re
from collections import Counter

LISTE = "liste/karsilastirma"
REHBER = "rehber/aciklama"
HIZMET = "hizmet/urun"
SOSYAL = "sosyal/video"
HABER = "haber"
DIGER = "diger"

# 1) LISTE/KARSILASTIRMA — en spesifik, once bakilir.
#    "En iyi 10 ...", "Top 15 tools", "X vs Y", "... Firmalar (2026 Guncel Liste)"
_LISTE_BASLIK = re.compile(
    r"(?i)(en\s*i̇?yi|en\s*guvenli|en\s*güvenli|best|top)\s*\d"          # "En iyi 10"
    r"|^\s*\d+\s*[\w\s]{0,20}(arac|araç|tool|platform|firma|ajans|uygulama|picks|best|secenek)"
    r"|\b\d+\s*(best|top)\b"
    r"|\bvs\.?\b|compared|comparison|karsilastirma|karşılaştırma|kiyas|kıyas"
    r"|guncel\s*liste|güncel\s*liste|ranked|siralama|sıralama"
    # Elle denetimden gelen kalip: superlatif + COGUL varlik ("... Veren Firmalar",
    # "En Guvenli Mesajlasma Uygulamalari") — sayi olmasa da liste sayfasidir.
    r"|(en\s*i̇?yi|en\s*guvenli|en\s*güvenli|veren|sunan)\s+[\w\s]{0,30}"
    r"(firmalar|ajanslar|araclar|araçlar|uygulamalar|hizmetler|uzmanlar|platformlar|servisler)"
)
_LISTE_URL = re.compile(r"(?i)/list/|best-|top-|en-iyi|-karsilastirma|-comparison|/picks/|alternatif")

# 2) REHBER/ACIKLAMA — "X nedir", "nasil", "guide"
_REHBER = re.compile(r"(?i)\bnedir\b|rehber|guide|kilavuz|kılavuz|how\s+to|nasil|nasıl|explained|101\b")

# 3) HIZMET/URUN — danismanlik, ajans hizmet sayfasi, urun ana sayfasi
_HIZMET = re.compile(r"(?i)danışman|danisman|hizmet(i|leri)?\b|service|ajans[ıi]?\b|uzman[ıi]?\b|/mail\b|pricing|fiyat")

_SOSYAL_HOST = re.compile(r"(?i)(^|\.)(youtube\.com|instagram\.com|linkedin\.com|medium\.com|reddit\.com|x\.com|twitter\.com|tiktok\.com)")
_HABER = re.compile(r"(?i)haber|sondakika|son-dakika|dergisi|gazete|/news/")


def siniflandir(url: str, baslik: str = "") -> str:
    """Tek kaynagi sayfa tipine ayirir. Sira ONEMLI: en spesifik once."""
    u = (url or "").strip()
    b = (baslik or "").strip()
    hepsi = f"{b} {u}"

    if _LISTE_BASLIK.search(b) or _LISTE_URL.search(u):
        return LISTE
    # Sosyal host'u rehber/hizmetten ONCE bakma: LinkedIn'de de "En iyi 10"
    # yazisi olabilir ve o bir LISTEDIR (yukaridaki dal onu zaten yakalar).
    if _SOSYAL_HOST.search(u):
        return SOSYAL
    if _HABER.search(hepsi):
        return HABER
    if _REHBER.search(hepsi):
        return REHBER
    if _HIZMET.search(hepsi):
        return HIZMET
    # Ciplak ana sayfa (yol yok ya da "/") + kisa baslik -> urun/marka sayfasi.
    # Elle denetimden: "Mextup: Mektup Yaz", "DocuPost", "Sprout Social" boyleydi.
    # 🪤 Once GERCEK bir adres olmali: bos/bozuk url "ciplak ana sayfa" sayilip
    # urun diye siniflanmamali (test yakaladi).
    if re.match(r"(?i)^https?://[^/]+", u):
        yol = re.sub(r"(?i)^https?://[^/]+", "", u)
        if yol in ("", "/") and len(b) <= 60:
            return HIZMET
    return DIGER


def dagilim(kaynaklar: list[dict]) -> dict | None:
    """
    `kaynaklar`: [{"url": str, "title"/"baslik": str}, ...]
    Doner: tip dagilimi (TEKIL URL bazinda — ayni sayfanin bircok sorguda
    cikmasi dagilimi sismesin) + ham sayim.
    """
    tekil: dict[str, str] = {}
    for k in (kaynaklar or []):
        u = (k or {}).get("url")
        if not u:
            continue
        tekil[u] = siniflandir(u, (k.get("title") or k.get("baslik") or ""))
    if not tekil:
        return None
    c = Counter(tekil.values())
    n = sum(c.values())
    return {
        "tekil_sayfa": n,
        "dagilim": {tip: adet for tip, adet in c.most_common()},
        "oran": {tip: round(adet / n, 3) for tip, adet in c.most_common()},
        "baskin_tip": c.most_common(1)[0][0],
        # Istemci "deneysel · skora katilmiyor" etiketi bassin.
        "shadow": True,
    }
