"""Bildirim esigi tarama tipine gore (kurucu karari 2026-08-04).

NEDEN: sosyal taramada crawl/indeks/skorlama yok; genel skor dogrudan
brand_recall'dan gelir ve orada SOV agirligi 0.55 — site taramasindaki 0.075'in
yedi kati. Olculdu: SOV birincil hucresi 8, tek hucrenin oynamasi genel skoru
~6,9 puan degistiriyor. Esik 5 iken bu, hicbir sey degismemisken "skorun dustu"
bildirimi demekti.

Bu testler sayiyi degil KURALI kilitler: esik, tek hucrenin karsiligindan
BUYUK, iki hucreninkinden KUCUK olmali. SOV agirligi ya da hucre sayisi
degisirse burasi kirilir ve esik yeniden hesaplanir.
"""
from pathlib import Path

import monitor

_KOK = Path(__file__).resolve().parent.parent

# Olculen degerler (2026-08-03 canli tarama + WEIGHTS_SOCIAL)
SOSYAL_SOV_AGIRLIK = 0.55
BIRINCIL_HUCRE = 8
TEK_HUCRE_SOV = 100.0 / BIRINCIL_HUCRE          # 12,5 SOV puani
TEK_HUCRE_GENEL = TEK_HUCRE_SOV * SOSYAL_SOV_AGIRLIK   # ~6,9 genel puan


def test_sosyal_esik_tek_hucre_gurultusunu_susturur():
    assert monitor.SOCIAL_SCORE_CHANGE_THRESHOLD > TEK_HUCRE_GENEL, (
        f"tek hucre {TEK_HUCRE_GENEL:.1f} puan oynatiyor; esik bunun ustunde "
        "olmali yoksa hicbir sey degismemisken bildirim gider")


def test_sosyal_esik_iki_hucreyi_hala_duyurur():
    assert monitor.SOCIAL_SCORE_CHANGE_THRESHOLD < 2 * TEK_HUCRE_GENEL, (
        f"iki hucre {2 * TEK_HUCRE_GENEL:.1f} puan; esik bunun altinda olmali "
        "yoksa gercek degisim de sessiz kalir")


def test_web_esigi_degismedi():
    """Site/kisi/marka tarafinda SOV seyreltilmis (0.075) — esik 5 kalmali."""
    assert monitor.SCORE_CHANGE_THRESHOLD == 5


def test_esik_secimi_tipe_bagli():
    """Kod sosyal tipi ayirt etmeli; etmezse sosyal yine 5'e duser."""
    kaynak = (_KOK / "monitor.py").read_text(encoding="utf-8")
    assert 'item.get("type") == "social"' in kaynak
    assert ">= esik" in kaynak, "esik degiskeni kullanilmiyor"
