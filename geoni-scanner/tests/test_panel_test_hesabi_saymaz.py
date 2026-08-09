"""Admin paneli TEST HESAPLARINI saymaz (kurucu karari 2026-08-09).

YASANDI: panel 46 uye ve "bu hafta 22 yeni uye" gosteriyordu; gercegi 26 ve 2.
Aradaki 20 kayit ajan oturumlarinin actigi test hesabiydi. Kayit nobeti onlari
zaten disliyordu — dislamayan TEK yer paneldi. Kurucu "isaretle" dedi (silme
degil), `profiles.test_hesap` kolonu eklendi.

Bu test sorgularin suzgeci TASIDIGINI kilitler; gercek Supabase gerekmez.
"""
import asyncio

import db


def _sorgulari_topla(monkeypatch) -> list[str]:
    yollar: list[str] = []

    async def sahte_count(yol: str) -> int:
        yollar.append(yol)
        return 0

    async def sahte_returning(a, b) -> int:
        return 0

    monkeypatch.setattr(db, "_count", sahte_count)
    monkeypatch.setattr(db, "_returning_users", sahte_returning)
    asyncio.run(db.get_admin_summary())
    return yollar


def test_uye_sayimlari_test_hesabini_disliyor(monkeypatch):
    yollar = _sorgulari_topla(monkeypatch)
    profil_sorgulari = [y for y in yollar if y.startswith("profiles")]
    assert profil_sorgulari, "profiles sayimi hic yapilmadi"
    for y in profil_sorgulari:
        assert db.GERCEK_UYE in y, f"suzgec YOK: {y}"


def test_tarama_sayimi_suzgecsiz_KALIR(monkeypatch):
    """audits sayimi profillere gore suzulmez — kural yanlis yere tasinmasin."""
    yollar = _sorgulari_topla(monkeypatch)
    audit_sorgulari = [y for y in yollar if y.startswith("audits")]
    assert audit_sorgulari and all(db.GERCEK_UYE not in y for y in audit_sorgulari)


def test_suzgec_TEK_yerde_tanimli():
    """Ayni kuralin iki yerde yasamasi bu projede dort kez kusur uretti."""
    assert db.GERCEK_UYE == "test_hesap=is.false"
    kaynak = open("db.py", encoding="utf-8").read()
    assert kaynak.count('"test_hesap=is.false"') == 1, "suzgec metni birden fazla yerde yazili"
