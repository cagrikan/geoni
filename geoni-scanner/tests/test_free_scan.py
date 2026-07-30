"""
free_scan.free_scan_gate + record_free_scan karar mantigi (max guvenlik:
cihaz + hesap). Bagimliliklar (db + devicecheck) mock'lanir; saf mantik test.
pytest-asyncio'ya bagimli olmamak icin asyncio.run() ile cagrilir.

Cihaz katmani artik SAYAC degil TEK BIT: 2 bit ekip (2Y6PBTM588) basina ve
bit0 daktilo'nun. GEONI yalnizca bit1'i sahiplenir, hak = 1.
Bkz. devicecheck.py modul basligi + memory/devicecheck-bit-sozlesmesi.md.
"""
import asyncio

import pytest

import free_scan


def _install(monkeypatch, *, premium=False, device_state={"used": False, "other": False},
             account_used=0, limit=1, creator=False, creator_used=0, balance=0):
    """Katmanlari sahte async fonksiyonlarla degistirir; cagri kayitlarini döner."""
    rec = {"inc": [], "mark": []}

    async def _creator(uid):
        return creator

    async def _aylik(uid):
        return creator_used

    async def _premium(uid):
        return premium

    async def _acct(uid):
        return account_used

    async def _inc(uid):
        rec["inc"].append(uid)
        return account_used + 1

    async def _bakiye(uid):
        return balance

    async def _qdev(token):
        return device_state

    async def _mark(token, state):
        rec["mark"].append((token, state))
        return True

    monkeypatch.setattr(free_scan, "is_barter_creator", _creator)
    monkeypatch.setattr(free_scan, "count_scans_this_month", _aylik)
    monkeypatch.setattr(free_scan, "CREATOR_MONTHLY_SCANS", 30)
    monkeypatch.setattr(free_scan, "check_is_premium", _premium)
    monkeypatch.setattr(free_scan, "get_credit_balance", _bakiye)
    monkeypatch.setattr(free_scan, "get_free_scans_used", _acct)
    monkeypatch.setattr(free_scan, "increment_free_scans", _inc)
    monkeypatch.setattr(free_scan, "query_device_state", _qdev)
    monkeypatch.setattr(free_scan, "mark_device_used", _mark)
    monkeypatch.setattr(free_scan, "FREE_SCAN_LIMIT", limit)
    return rec


def test_premium_bypasses_cap(monkeypatch):
    rec = _install(monkeypatch, premium=True, device_state={"used": True, "other": True},
                   account_used=9)
    allowed, info = asyncio.run(free_scan.free_scan_gate("u1", "devtok"))
    assert allowed and info["reason"] == "premium"
    asyncio.run(free_scan.record_free_scan("u1", "devtok", info))
    assert rec["inc"] == [] and rec["mark"] == []  # premium → record no-op


def test_anon_new_device_allowed_and_marks(monkeypatch):
    rec = _install(monkeypatch, device_state={"used": False, "other": False})
    allowed, info = asyncio.run(free_scan.free_scan_gate(None, "devtok"))  # anonim
    assert allowed
    asyncio.run(free_scan.record_free_scan(None, "devtok", info))
    assert rec["inc"] == []                                    # anonim → hesap sayaci artmaz
    assert rec["mark"] == [("devtok", {"used": False, "other": False})]


def test_device_bit_set_blocks(monkeypatch):
    _install(monkeypatch, device_state={"used": True, "other": False})
    allowed, info = asyncio.run(free_scan.free_scan_gate(None, "devtok"))
    assert not allowed and info["reason"] == "free_limit_reached"
    assert info["device_over"] and not info["account_over"]


def test_daktilo_bit_does_not_block_geoni(monkeypatch):
    """CAKISMA REGRESYONU: daktilo hakkini kullanmis cihaz (bit0=1) GEONI'de
    HÂLÂ ucretsiz tarama alabilmeli. Eski sayac mantiginda count=1 >= limit
    olup bloklaniyordu — bu testin varlik sebebi tam olarak o."""
    rec = _install(monkeypatch, device_state={"used": False, "other": True})
    allowed, info = asyncio.run(free_scan.free_scan_gate(None, "devtok"))
    assert allowed and info["reason"] == "ok"
    asyncio.run(free_scan.record_free_scan(None, "devtok", info))
    # ve yazarken daktilo'nun biti KORUNUR
    assert rec["mark"] == [("devtok", {"used": False, "other": True})]


def test_account_at_limit_blocks_even_new_device(monkeypatch):
    _install(monkeypatch, device_state={"used": False, "other": False}, account_used=1)
    allowed, info = asyncio.run(free_scan.free_scan_gate("u1", "devtok"))
    assert not allowed and info["account_over"]


def test_device_unknown_safe_side_account_decides(monkeypatch):
    # DeviceCheck env yok → query None → cihaz katmani atlanir, hesap karar verir
    rec = _install(monkeypatch, device_state=None, account_used=0)
    allowed, info = asyncio.run(free_scan.free_scan_gate("u1", None))
    assert allowed
    asyncio.run(free_scan.record_free_scan("u1", None, info))
    assert rec["inc"] == ["u1"]                     # hesap +1
    assert rec["mark"] == []                        # durum bilinmeden YAZILMAZ


def test_logged_in_both_recorded(monkeypatch):
    rec = _install(monkeypatch, device_state={"used": False, "other": True}, account_used=0)
    allowed, info = asyncio.run(free_scan.free_scan_gate("u1", "devtok"))
    assert allowed
    asyncio.run(free_scan.record_free_scan("u1", "devtok", info))
    assert rec["inc"] == ["u1"]                     # hesap +1
    assert rec["mark"] == [("devtok", {"used": False, "other": True})]


# ── Creator (barter) aylik kotasi: isbirligi.html'de "ayda 30 tarama" vaadi ──

def test_creator_under_quota_allowed_and_no_counters_touched(monkeypatch):
    rec = _install(monkeypatch, creator=True, creator_used=29,
                   device_state={"used": True, "other": True}, account_used=99)
    allowed, info = asyncio.run(free_scan.free_scan_gate("u1", "devtok"))
    # Cihaz biti dolu ve hesap sayaci tavanda olsa BILE creator gecer:
    # kota kontrolu bu iki katmandan ONCE calisir.
    assert allowed and info["reason"] == "creator" and info["limit"] == 30
    asyncio.run(free_scan.record_free_scan("u1", "devtok", info))
    # Turev sayim kullanildigi icin tutulacak sayac yok → ikisi de dokunulmamali
    assert rec["inc"] == [] and rec["mark"] == []


def test_creator_at_quota_blocked_with_monthly_limit(monkeypatch):
    _install(monkeypatch, creator=True, creator_used=30)
    allowed, info = asyncio.run(free_scan.free_scan_gate("u1", "devtok"))
    assert not allowed and info["reason"] == "free_limit_reached"
    assert info["limit"] == 30 and info["creator"] is True


def test_creator_count_failure_is_safe_side(monkeypatch):
    """Sayim yapilamazsa kota DOLMUS sayilir. Tersi (izin ver) sayim hatasini
    sinirsiz ucretsiz taramaya cevirirdi ve her tarama gercek para yakiyor."""
    _install(monkeypatch, creator=True, creator_used=None)
    allowed, info = asyncio.run(free_scan.free_scan_gate("u1", "devtok"))
    assert not allowed and info["creator"] is True


def test_premium_beats_creator(monkeypatch):
    """Kredi almis creator kotaya takilmamali — premium kontrolu once gelir."""
    _install(monkeypatch, premium=True, creator=True, creator_used=99)
    allowed, info = asyncio.run(free_scan.free_scan_gate("u1", "devtok"))
    assert allowed and info["reason"] == "premium"


def test_non_creator_unaffected(monkeypatch):
    """Creator olmayan kullanicida yol aynen eski davranis."""
    _install(monkeypatch, creator=False, device_state={"used": False, "other": False})
    allowed, info = asyncio.run(free_scan.free_scan_gate("u1", "devtok"))
    assert allowed and info["reason"] == "ok"


# ── ODENEN tarama ucretsiz tarama degildir (2026-07-30) ─────────────────
# Kapi yalnizca check_is_premium'e bakiyordu (= `total_credits_purchased > 0`).
# Hediye/referral tokenu olan (para odememis) kullanicinin ilk taramasi hem
# token yakiyor HEM omurluk ucretsiz hakkini harciyordu; ikincisinde cebinde
# token varken `free_limit_reached` yiyordu -> referral odulu dogdugu anda olu.

def test_bakiye_yetiyorsa_tavan_uygulanmaz(monkeypatch):
    """Hakkini kullanmis ama tokeni olan kullanici taramaya DEVAM edebilmeli."""
    rec = _install(monkeypatch, account_used=1, limit=1, balance=20)
    allowed, info = asyncio.run(free_scan.free_scan_gate("u1", None, scan_cost=5))
    assert allowed and info["reason"] == "paid"


def test_odenen_tarama_ucretsiz_hakki_yakmaz(monkeypatch):
    """Asil kayip buydu: token ODENEN tarama sayaci artiriyordu."""
    rec = _install(monkeypatch, account_used=0, limit=1, balance=20)
    allowed, info = asyncio.run(free_scan.free_scan_gate("u1", "devtok", scan_cost=5))
    assert allowed and info["reason"] == "paid"
    asyncio.run(free_scan.record_free_scan("u1", "devtok", info))
    assert rec["inc"] == [] and rec["mark"] == []


def test_bakiye_tam_bedel_kadarsa_yeter(monkeypatch):
    _install(monkeypatch, account_used=1, limit=1, balance=10)
    allowed, info = asyncio.run(free_scan.free_scan_gate("u1", None, scan_cost=10))
    assert allowed and info["reason"] == "paid"


def test_bakiye_yetmezse_tavan_aynen_uygulanir(monkeypatch):
    """4 token ile 5 tokenlik tarama alinamaz -> bu gercekten UCRETSIZ tarama."""
    _install(monkeypatch, account_used=1, limit=1, balance=4)
    allowed, info = asyncio.run(free_scan.free_scan_gate("u1", None, scan_cost=5))
    assert not allowed and info["reason"] == "free_limit_reached"


def test_sosyal_tarama_bedava_oldugu_icin_tavana_tabi(monkeypatch):
    """Sosyal tarama token DUSMEZ (save_brand_check deduct=not social) -> cost 0.
    Bakiyesi olsa bile bedava oldugu icin tavan uygulanmali; aksi halde bakiyesi
    olan herkes sinirsiz bedava sosyal tarama yapardi."""
    _install(monkeypatch, account_used=1, limit=1, balance=9999)
    allowed, info = asyncio.run(free_scan.free_scan_gate("u1", None, scan_cost=0))
    assert not allowed and info["reason"] == "free_limit_reached"


def test_anonim_kullanici_bakiyeden_yararlanamaz(monkeypatch):
    """user_id yoksa odeyecek hesap da yok -> tavan aynen."""
    _install(monkeypatch, account_used=0, limit=1, balance=9999,
             device_state={"used": True, "other": False})
    allowed, info = asyncio.run(free_scan.free_scan_gate(None, "devtok", scan_cost=5))
    assert not allowed and info["reason"] == "free_limit_reached"


def test_bedel_varsayilani_sifir_eski_davranis(monkeypatch):
    """scan_cost verilmezse (bedava tarama) davranis DEGISMEMELI."""
    _install(monkeypatch, account_used=1, limit=1, balance=9999)
    allowed, info = asyncio.run(free_scan.free_scan_gate("u1", None))
    assert not allowed and info["reason"] == "free_limit_reached"


# ── Bedel surukleme korumasi ────────────────────────────────────────────
def test_kapinin_bildigi_bedel_gercek_dusumle_ayni():
    """Kapi "bu tarama odenecek mi" kararini main.py'deki sabitlere gore veriyor,
    gercek dusum ise db.py icinde. Ikisi ayrisirsa kapi YANLIS karar verir:
    bedel dususe (or. 5->3) tavan gereksiz uygulanir; artarsa bakiyesi yetmeyen
    kullanici tavani atlar ve odemeden tarar."""
    import pathlib
    import re

    import main

    db_kaynak = (pathlib.Path(main.__file__).parent / "db.py").read_text(encoding="utf-8")
    assert f'deduct_credits(user_id, {main.WEB_SCAN_COST}, "web_audit"' in db_kaynak, \
        "save_audit'teki web bedeli main.WEB_SCAN_COST ile ayni degil"
    # save_brand_check: `credits = 10 if (deduct and user_id) else 0`
    marka = re.search(r"credits = (\d+) if \(deduct and user_id\) else 0", db_kaynak)
    assert marka, "save_brand_check'teki bedel satiri bulunamadi (yeniden yazilmis?)"
    assert int(marka.group(1)) == main.BRAND_SCAN_COST, \
        f"marka bedeli db.py'de {marka.group(1)}, main.py'de {main.BRAND_SCAN_COST}"


def test_sosyal_tarama_hala_dusumsuz():
    """Sosyal taramaya bedel gelirse `scan_cost=0` varsayimi cokerdi."""
    import pathlib

    import main

    db_kaynak = (pathlib.Path(main.__file__).parent / "db.py").read_text(encoding="utf-8")
    assert "deduct=not bool(getattr(request, \"social\", False))" in \
        (pathlib.Path(main.__file__)).read_text(encoding="utf-8"), \
        "sosyal taramanin deduct=False kurali degismis"
    assert "credits = 10 if (deduct and user_id) else 0" in db_kaynak
