"""content_decay: GEZILEMEYEN tarama "icerik bayatliyor" sayilmaz.

NE OLDU (2026-08-06 dongusunde canli veriyle olculdu):
`_content_decay_signals` domain basina en yeni iki taramanin freshness'ini
kiyasliyordu ama taramanin SAYFA GEZIP GEZMEDIGINE bakmiyordu.

`compute_freshness_score` sayfa yoksa NOTR **50.0** doner — bu bir olcum degil,
yer tutucu (bkz. test_olculemedi_sifir_degil.py). Taramalarin ~%35'i bot
korumasi/WAF/JS-only render yuzunden 0 sayfayla bitiyor. Sonuc: daha once 100
olcmus bir site, gezilemedigi turda 50'ye "duserek" bayatlamis gibi gorunuyordu.

O dongude uretilen 3 sinyalin 2'si tam bu sahte durumdu:
  · doktortakvimi.com  100  -> 50   (son tarama 0 sayfa)
  · nuxt.com           89.4 -> 50   (son tarama 0 sayfa)
  · dopinger.com        30  -> 20   (20 sayfa — GERCEK dusus, kalmali)

Yani yanlis-pozitif orani %67'ydi. Sinyal `mode: otonom` ve kurucuya haftalik
e-posta ozetine giriyor ("⚠️ Icerigi bayatlayan hedefler"), yani yanlis sinyal
dogrudan kurucunun dikkatini yaniltiyordu.

KURAL: freshness ancak GERCEKTEN sayfa gezilmis taramalardan kiyaslanir.
"""
from self_improve import decay_from_rows


def _satir(domain, freshness, sayfa_sayisi):
    return {
        "domain": domain,
        "result_json": {
            "pages": [{"url": f"https://{domain}/{i}"} for i in range(sayfa_sayisi)],
            "score_breakdown": {"freshness": freshness},
        },
    }


def test_gezilemeyen_son_tarama_sinyal_URETMEZ():
    """Asil dava: 100 -> 50 dususu, son tarama 0 sayfaysa bayatlama DEGILDIR."""
    rows = [_satir("doktortakvimi.com", 50.0, 0),    # en yeni: gezilemedi
            _satir("doktortakvimi.com", 100.0, 12)]  # onceki: gercek olcum
    assert decay_from_rows(rows) == []


def test_gercek_dusus_HALA_yakalaniyor():
    """Duzeltme yalniz 0-sayfa dalini etkiler; olculmus dusus kaybolmamali."""
    rows = [_satir("dopinger.com", 20.0, 20),
            _satir("dopinger.com", 30.0, 18)]
    (sinyal,) = decay_from_rows(rows)
    assert sinyal["kind"] == "content_decay"
    assert sinyal["subject"] == "dopinger.com"
    assert sinyal["metric"] == -10.0
    assert sinyal["detail"] == {"latest": 20.0, "previous": 30.0, "scans": 2}


def test_gezilemeyen_ONCEKI_tarama_da_atlanir():
    """Yer tutucu 50 kiyasin OTEKI ucunda da olabilir: 50 -> 20 "dusus" gibi
    gorunur ama 50 hic olculmemisti. O satir tamamen atlanir; geriye tek olculmus
    tarama kalir ve sinyal uretilmez."""
    rows = [_satir("x.com", 20.0, 9),
            _satir("x.com", 50.0, 0)]
    assert decay_from_rows(rows) == []


def test_esik_alti_dusus_sinyal_degil():
    """5 puandan kucuk oynama gurultudur."""
    rows = [_satir("y.com", 47.0, 5), _satir("y.com", 50.0, 5)]
    assert decay_from_rows(rows) == []


def test_yukselis_sinyal_degil():
    rows = [_satir("z.com", 90.0, 5), _satir("z.com", 40.0, 5)]
    assert decay_from_rows(rows) == []


def test_tek_tarama_sinyal_degil():
    assert decay_from_rows([_satir("tek.com", 10.0, 3)]) == []


def test_bozuk_satirlar_patlatmaz():
    """retention eski satirlarin result_json'unu NULL'luyor (2026-08-06'da
    doktortakvimi/nuxt satirlarinda dogrulandi) — dongu bunlara dayanikli olmali."""
    rows = [{"domain": "a.com", "result_json": None},
            {"domain": "a.com", "result_json": "bozuk-metin"},
            {"domain": None, "result_json": {"pages": [{"url": "u"}],
                                             "score_breakdown": {"freshness": 10}}},
            {"result_json": {"pages": [{"url": "u"}], "score_breakdown": {}}},
            _satir("a.com", 20.0, 4), _satir("a.com", 80.0, 4)]
    (sinyal,) = decay_from_rows(rows)
    assert sinyal["subject"] == "a.com" and sinyal["metric"] == -60.0
