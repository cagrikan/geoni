"""Oz-gelisim korpusu test hesaplarindan arindirilmis olmali.

NEDEN (2026-08-10): motor son 7 gunun taramalarini suzmeden cekiyordu ve
25 taramanin 10'u ajan test hesabina aitti (%40). content_gap sinyali otonom
icerik plani uretiyor — kirli korpus urun kararini bozuyordu.
"""
from self_improve import gercek_kullanici_taramalari


def test_test_hesabinin_taramasi_elenir():
    rows = [
        {"user_id": "t1", "result_json": {}},
        {"user_id": "gercek", "result_json": {}},
    ]
    out = gercek_kullanici_taramalari(rows, {"t1"})
    assert [r["user_id"] for r in out] == ["gercek"]


def test_anonim_tarama_KALIR():
    """user_id yok = gercek ucretsiz tarama; elenmemeli."""
    rows = [{"user_id": None, "result_json": {}}]
    assert len(gercek_kullanici_taramalari(rows, {"t1"})) == 1


def test_bos_kimlik_listesi_hicbir_seyi_elemez():
    rows = [{"user_id": "a"}, {"user_id": "b"}]
    assert len(gercek_kullanici_taramalari(rows, set())) == 2


def test_korpus_tamamen_test_hesabiysa_bos_doner():
    """Sinir durum: sifir satir kalirsa dongy patlamamali, bos donmeli."""
    rows = [{"user_id": "t1"}, {"user_id": "t2"}]
    assert gercek_kullanici_taramalari(rows, {"t1", "t2"}) == []
