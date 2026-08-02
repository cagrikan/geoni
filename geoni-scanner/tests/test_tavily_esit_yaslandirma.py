"""Tavily anahtarlari esit yaslanmali (2026-08-02).

YASANDI: `_tavily_rr` surec-ici bir sayac ve 0'dan basliyordu. Worker
scale-to-zero ile calistigi icin neredeyse her tarama YENI bir gorevde kosuyor,
sayac 0'a donuyor ve `0 % 2 = 0` -> her taramanin ilk cagrisi hep tavily-1'e
gidiyordu. Canli olcum: son 7 gunde 21 cagrinin 21'i tavily-1; tavily-2
30 Temmuz'dan beri hic kullanilmadi (toplam 228 vs 133).

Erisilebilirlik sorunu DEGILDI (kota dolunca failover devrede) ama kotalar
esit yaslanmiyordu. Bu dosya hem donusumun hem de rastgele baslangicin
korundugunu kanitlar.
"""
import importlib
import os

import brand_recall


def _iki_anahtarla_yukle(monkeypatch, seed=None):
    """brand_recall'i iki Tavily anahtariyla yeniden yukler (modul seviyesi
    sabitler import aninda hesaplaniyor)."""
    monkeypatch.setenv("TAVILY_API_KEY", "k1")
    monkeypatch.setenv("TAVILY_API_KEY_2", "k2")
    if seed is not None:
        import random
        random.seed(seed)
    return importlib.reload(brand_recall)


def test_donusumlu_kullanim_devam_ediyor(monkeypatch):
    """Ardisik cagrilar anahtarlar arasinda donmeli."""
    m = _iki_anahtarla_yukle(monkeypatch)
    etiketler = [m._next_tavily_key()[1] for _ in range(6)]
    assert set(etiketler) == {"tavily-1", "tavily-2"}, etiketler
    # ardisik ikili hep farkli olmali
    for a, b in zip(etiketler, etiketler[1:]):
        assert a != b, f"ust uste ayni anahtar: {etiketler}"


def test_baslangic_noktasi_HER_ZAMAN_SIFIR_DEGIL(monkeypatch):
    """
    Asil regresyon kalkani. Farkli tohumlarla surec basi baslangici degismeli;
    hep 0 olsaydi scale-to-zero altinda tum ilk-cagrilar tavily-1'e giderdi.
    """
    baslangiclar = set()
    for seed in range(40):
        m = _iki_anahtarla_yukle(monkeypatch, seed=seed)
        baslangiclar.add(m._tavily_rr["i"] % len(m.TAVILY_API_KEYS))
    assert baslangiclar == {0, 1}, f"baslangic hep ayni: {baslangiclar}"


def test_ilk_cagri_iki_anahtara_da_dagiliyor(monkeypatch):
    """
    Gercek senaryonun taklidi: her 'tarama' yeni bir surec (reload) ve yalnizca
    ILK cagriyi yapiyor. Ikisi de kullanilmali.
    """
    ilkler = []
    for seed in range(30):
        m = _iki_anahtarla_yukle(monkeypatch, seed=seed)
        ilkler.append(m._next_tavily_key()[1])
    assert set(ilkler) == {"tavily-1", "tavily-2"}, f"ilk cagrilar tek anahtarda: {set(ilkler)}"
    # kaba denge: hicbiri %90'i gecmesin
    for etiket in ("tavily-1", "tavily-2"):
        oran = ilkler.count(etiket) / len(ilkler)
        assert oran < 0.9, f"{etiket} orani {oran:.0%} — dagilim dengesiz"


def test_tek_anahtarla_bozulmaz(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k1")
    monkeypatch.delenv("TAVILY_API_KEY_2", raising=False)
    m = importlib.reload(brand_recall)
    assert m._next_tavily_key()[1] == "tavily-1"
    assert m._next_tavily_key()[1] == "tavily-1"


def test_anahtar_yoksa_bos_doner_patlamaz(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY_2", raising=False)
    m = importlib.reload(brand_recall)
    assert m._next_tavily_key() == ("", "")
    assert m._tavily_rr["i"] == 0   # anahtar yokken randrange cagrilmamali


def teardown_module(_m):
    """Diger testler modulun gercek halini gormeli."""
    for v in ("TAVILY_API_KEY", "TAVILY_API_KEY_2"):
        os.environ.pop(v, None)
    importlib.reload(brand_recall)
