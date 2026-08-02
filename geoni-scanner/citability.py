"""
GEONI Scanner — Alintilanabilirlik + yapisal okunabilirlik (2026-08-02).

NEDEN VAR: urunun vaadi "AI seni ALINTILASIN". Buna ragmen sayfanin
alintilanabilir olup olmadigina hic bakmiyorduk — semaya, tazelige, bot
erisimine bakiyorduk. claude-seo karsilastirmasinda cikan en buyuk bosluk buydu
(onlarin modelinde Citability 0.25 + Structural 0.20, bizde ikisi de YOK).

Dayandigi bulgular (claude-seo v2.2.4 seo-geo skill'inin KAYNAK GOSTERDIGI
degerler; tarafimizdan BAGIMSIZ DOGRULANMADI, o yuzden skora degil once
RAPORA yaziliyor):
  - Alintilanan pasajlar icin ideal uzunluk 134-167 kelime
  - AI atiflarinin ~%44'u sayfanin ilk %30'undan geliyor (SE Ranking)
  - Bolumun ilk 40-60 kelimesinde dogrudan cevap olmali
  - Soru bicimli basliklar sorgu kaliplarina esler

GOLGE MOD: bu modul skoru DEGISTIRMEZ. Grok ve AI Overview'da izledigimiz
desen: once olc, veri biriksin, redundansi olcelim, WEIGHTS ancak ondan sonra
konusulsun. Dogrulamadigimiz bir arastirmayi bugun skora sokmak, kendi
kuralimizi (olcmediysen soyleme) cignemek olurdu.

Playwright ISTEMEZ: girdi crawler'in cikardigi yapisal ozetlerdir, boylece
CI'nin minimal ortaminda test edilebilir.
"""

import re
import statistics

# seo-geo'nun bildirdigi ideal pasaj araligi. Kenarlar yumusak: 100-220 arasi
# "kabul edilebilir" sayilir, disi cezalandirilmaz sadece isaretlenir.
IDEAL_MIN, IDEAL_MAX = 134, 167
KABUL_MIN, KABUL_MAX = 100, 220

# Bir bolumun basindaki "dogrudan cevap" penceresi.
CEVAP_PENCERESI = 60

_SORU_ISARETLERI = (
    "?", " nedir", " nasil", " nasıl", " neden", " hangi", " kim ", " ne zaman",
    " what ", " how ", " why ", " which ", " who ", " when ",
)


def _kelime_say(metin: str) -> int:
    return len([w for w in re.split(r"\s+", (metin or "").strip()) if w])


def soru_basligi_mi(baslik: str) -> bool:
    """Soru bicimli baslik, kullanicinin sorgusuna dogrudan esler."""
    b = f" {(baslik or '').strip().lower()} "
    return any(im in b for im in _SORU_ISARETLERI)


def analiz_et(sayfa: dict) -> dict | None:
    """
    `sayfa`: {"headings": [{"level": int, "text": str}, ...],
              "paragraphs": [str, ...],
              "list_count": int, "table_count": int, "faq": bool}

    Doner: alintilanabilirlik + yapi ozeti. Veri yoksa None.
    """
    basliklar = sayfa.get("headings") or []
    paragraflar = [p for p in (sayfa.get("paragraphs") or []) if (p or "").strip()]
    if not basliklar and not paragraflar:
        return None

    uzunluklar = [_kelime_say(p) for p in paragraflar]
    ideal = sum(1 for u in uzunluklar if IDEAL_MIN <= u <= IDEAL_MAX)
    kabul = sum(1 for u in uzunluklar if KABUL_MIN <= u <= KABUL_MAX)

    # Atiflarin ~%44'u ilk %30'dan geliyor -> sayfanin ust ucte birinde
    # alintilanabilir bir blok var mi?
    ust_sinir = max(1, round(len(paragraflar) * 0.30))
    ust_bloklar = uzunluklar[:ust_sinir]
    ust_ideal = any(KABUL_MIN <= u <= KABUL_MAX for u in ust_bloklar)

    # Ilk paragrafin ilk 60 kelimesi dogrudan cevap veriyor mu? Uzunluk
    # kabaca vekil: cok kisa (<20) = cevap degil, cok uzun = gomulmus cevap.
    ilk = uzunluklar[0] if uzunluklar else 0
    dogrudan_cevap = 20 <= ilk <= CEVAP_PENCERESI * 2

    seviyeler = [h.get("level") for h in basliklar if isinstance(h.get("level"), int)]
    h1_sayisi = seviyeler.count(1)
    # Hiyerarsi bozuk mu: bir seviye atlanmis mi (H2'den H4'e gibi)?
    atlama = any(b - a > 1 for a, b in zip(seviyeler, seviyeler[1:])) if len(seviyeler) > 1 else False
    soru_basliklari = sum(1 for h in basliklar if soru_basligi_mi(h.get("text", "")))

    return {
        "paragraf_sayisi": len(paragraflar),
        "ideal_pasaj": ideal,
        "kabul_pasaj": kabul,
        "ortanca_uzunluk": int(statistics.median(uzunluklar)) if uzunluklar else 0,
        "ust_bolumde_alintilanabilir": ust_ideal,
        "dogrudan_cevap": dogrudan_cevap,
        "h1_sayisi": h1_sayisi,
        "hiyerarsi_atlamasi": atlama,
        "soru_basligi": soru_basliklari,
        "liste_sayisi": int(sayfa.get("list_count") or 0),
        "tablo_sayisi": int(sayfa.get("table_count") or 0),
        "faq": bool(sayfa.get("faq")),
    }


def site_ozeti(sayfalar: list[dict]) -> dict | None:
    """Sayfa analizlerini site duzeyinde ozetler (rapor icin tek blok)."""
    analizler = [a for a in (analiz_et(p) for p in (sayfalar or [])) if a]
    if not analizler:
        return None
    n = len(analizler)

    def oran(anahtar) -> float:
        return round(sum(1 for a in analizler if a[anahtar]) / n, 2)

    toplam_par = sum(a["paragraf_sayisi"] for a in analizler)
    toplam_kabul = sum(a["kabul_pasaj"] for a in analizler)
    return {
        "sayfa": n,
        # Alintilanabilirlik: kabul edilebilir uzunlukta pasajlarin orani
        "alintilanabilir_oran": round(toplam_kabul / toplam_par, 2) if toplam_par else 0.0,
        "ortanca_pasaj_kelime": int(statistics.median([a["ortanca_uzunluk"] for a in analizler])),
        "ust_bolumde_alintilanabilir_oran": oran("ust_bolumde_alintilanabilir"),
        "dogrudan_cevap_oran": oran("dogrudan_cevap"),
        # Yapi
        "soru_basligi_toplam": sum(a["soru_basligi"] for a in analizler),
        "hiyerarsi_bozuk_oran": oran("hiyerarsi_atlamasi"),
        "coklu_h1_oran": round(sum(1 for a in analizler if a["h1_sayisi"] > 1) / n, 2),
        "tablo_veya_liste_oran": round(
            sum(1 for a in analizler if a["liste_sayisi"] or a["tablo_sayisi"]) / n, 2),
        "faq_oran": oran("faq"),
        # GOLGE MOD isareti: istemci "deneysel · skora katilmiyor" etiketi bassin.
        "shadow": True,
    }
