"""Q4 (2026-07-25): quality kanali YALNIZ tanıyan motorlarin dogrulugundan olusur.

Bug: judge, "bu kisiyi bilmiyorum" yanitina yuksek dogruluk verir (hakli — uydurmuyor).
Bu puan quality'ye girince GORUNMEZ olmak gorunurluk skorunu YUKSELTIYORDU (ters tesvik).
Canli ornek (Coskun Demirdag): tanimayanlar 85, taniyanlar 7.5 -> kalite 46.3 cikiyordu.
"""


def _quality(entries, only_recognized: bool):
    """entries: [(recognized, dogruluk)] — canli kodun dogruluk_values mantigi."""
    vals = [d for rec, d in entries if (rec or not only_recognized)]
    return sum(vals) / len(vals) if vals else None


def test_tanimayan_motor_kaliteyi_sismez():
    # 2 taniyan (dusuk dogruluk) + 2 tanimayan (yuksek dogruluk)
    entries = [(True, 7.5), (True, 7.5), (False, 85.0), (False, 85.0)]
    eski = _quality(entries, only_recognized=False)
    yeni = _quality(entries, only_recognized=True)
    assert eski == 46.25, "eski davranis: tanimayanlar kaliteyi sisiriyordu"
    assert yeni == 7.5, "yeni: yalniz taniyanlarin dogrulugu sayilir"
    assert yeni < eski, "duzeltme skoru DOGRU yone (asagi) cekmeli"


def test_hepsi_taniyorsa_degisim_yok():
    entries = [(True, 60.0), (True, 80.0)]
    assert _quality(entries, False) == _quality(entries, True) == 70.0


def test_hicbiri_tanimiyorsa_bos():
    # recognition_count==0 dalinda quality zaten 0.0'a sabitlenir (F-O3);
    # burada onemli olan: yeni mantik bos liste uretir, eski sisirilmis deger uretiyordu.
    entries = [(False, 90.0), (False, 85.0)]
    assert _quality(entries, True) is None
    assert _quality(entries, False) == 87.5
