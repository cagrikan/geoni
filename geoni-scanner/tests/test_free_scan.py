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
# NOT: `main` IMPORT EDILMEZ — deploy kapisi testleri minimal ortamda kosuyor
# (pytest/httpx/cbor2/cryptography, fastapi YOK). Kaynak dosyalar METIN olarak
# okunur; boylece bu korumalar tam da onemli olduklari yerde, CI'da kosar.
import pathlib
import re

_KOK = pathlib.Path(__file__).resolve().parent.parent


def _sabit(kaynak: str, ad: str) -> int:
    m = re.search(rf"^{ad} = (\d+)", kaynak, re.M)
    assert m, f"{ad} sabiti scan_costs.py'de bulunamadi (yeniden adlandirilmis?)"
    return int(m.group(1))


def test_bedeller_tek_kaynaktan_okunuyor():
    """Bedel artik scan_costs.py'de; main.py ve db.py onu IMPORT etmeli.

    Eskiden her dosya kendi sayisini tasiyordu ve biri degisince digeri sessizce
    kaliyordu: kapi "odenecek mi" kararini yanlis verir, `audits.credits_spent`
    gercek dusumden ayrisir (2026-07-28'deki 1120 hayali kredi bu bicimdeydi)."""
    main_kaynak = (_KOK / "main.py").read_text(encoding="utf-8")
    db_kaynak = (_KOK / "db.py").read_text(encoding="utf-8")

    assert "from scan_costs import" in main_kaynak, "main.py bedelleri tek kaynaktan almiyor"
    assert "from scan_costs import" in db_kaynak, "db.py bedelleri tek kaynaktan almiyor"


def test_bedel_hicbir_yerde_duz_sayi_olarak_yazilmamis():
    """Sabit adi yerine duz sayi yazilirsa tek-kaynak korumasi sessizce delinir."""
    kalip_dosya = {
        "db.py": [
            r'deduct_credits\(user_id, \d+, "web_audit"',
            r"credits = \d+ if \(deduct and user_id\) else 0",
            r'"credits_spent": \d+ if \(deduct and user_id\)',
        ],
        "main.py": [
            r'deduct_credits\(user_id, \d+, "web_audit_private"',
            r"deduct_credits\(user_id, \d+, f\"\{request\.type",
        ],
    }
    for dosya, kaliplar in kalip_dosya.items():
        kaynak = (_KOK / dosya).read_text(encoding="utf-8")
        for kalip in kaliplar:
            bulunan = re.search(kalip, kaynak)
            assert not bulunan, (
                f"{dosya}: bedel duz sayi olarak yazilmis -> {bulunan.group(0)!r}. "
                "scan_costs.py'deki sabiti kullan.")


def test_public_uc_gercek_bedelleri_donuyor():
    """Arayuz 'N token ~ kac tarama' hesabini bu uctan yapiyor. Uc gercek
    bedelden koparsa musteriye yanlis sayi gosterilir ve fark ancak sikayetle
    anlasilir."""
    import scan_costs

    web = _sabit((_KOK / "scan_costs.py").read_text(encoding="utf-8"), "WEB_SCAN_COST")
    marka = _sabit((_KOK / "scan_costs.py").read_text(encoding="utf-8"), "BRAND_SCAN_COST")
    assert scan_costs.SCAN_COSTS["web"] == web
    assert scan_costs.SCAN_COSTS["person"] == marka == scan_costs.SCAN_COSTS["brand"]
    # Sosyal bedel de uctan donmeli (2026-08-03: 0 degil, yari fiyat).
    sosyal = _sabit((_KOK / "scan_costs.py").read_text(encoding="utf-8"), "SOCIAL_SCAN_COST")
    assert scan_costs.SCAN_COSTS["social"] == sosyal


def test_sosyal_tarama_da_bedel_duser():
    """2026-08-03 (kurucu karari): sosyal tarama artik UCRETSIZ DEGIL.

    Once `deduct=not social` ile hic dusum yapilmiyordu ve kapiya `scan_cost=0`
    geciliyordu. "Bir kisi bir kere ucretsiz tarar" kurali tokenle uygulaninca
    bedava bir kanal birakmak o kuralin TEK DELIGI olurdu: login sonrasi
    sinirsiz sosyal tarama. Sosyal yari fiyat (10) — viral kanca korunur.

    Kapiya gecen bedel ile fiilen dusulen bedel AYNI kaynaktan gelmeli; ayrisirsa
    kullanici kapidan gecer ama bakiyesi yetmez (ya da tersi).
    """
    main_kaynak = (_KOK / "main.py").read_text(encoding="utf-8")
    db_kaynak = (_KOK / "db.py").read_text(encoding="utf-8")
    assert 'deduct=not bool(getattr(request, "social", False))' not in main_kaynak, \
        "sosyal tarama yine dusumsuz yapilmis — 20 token kuralinin deligi geri geldi"
    assert "scan_cost=SOCIAL_SCAN_COST" in main_kaynak, \
        "kapiya gercek sosyal bedel gecirilmeli (scan_cost=0 varsayimi olu)"
    assert 'SOCIAL_SCAN_COST if entity_type == "social"' in db_kaynak, \
        "dusum tarafi tipe gore bedel secmeli"


def test_sosyal_bedel_marka_bedelinden_dusuk():
    """Yari fiyat kurali: sosyal < marka. Esitlenirse viral kanca kapanir."""
    from scan_costs import SOCIAL_SCAN_COST, BRAND_SCAN_COST
    assert 0 < SOCIAL_SCAN_COST < BRAND_SCAN_COST


def test_login_duvari_mesaji_tarama_tipine_gore():
    """Uc uc da 401 doner ama metin AYNI olmamali.

    2026-08-03 canli dogrulamada yakalandi: anonim tarama kapatilinca mesaj uc
    yerden birden donmeye basladi ama metni yalnizca marka akisini anlatiyordu —
    site taramasi yapan kullanici "Kişi/marka taraması için giriş yapın"
    goruyordu.

    NOT: `import main` YAPILMAZ — main.py fastapi/pydantic cekiyor, deploy
    kapisi testleri minimal ortamda kosuyor (bkz. scan_costs.py basligi ve
    [[feedback-ci-ortaminda-dogrula]]). Kaynak metni uzerinden dogrulanir.
    """
    kaynak = (_KOK / "main.py").read_text(encoding="utf-8")
    # Fonksiyon tip parametresi almali ve uc tipi de tanimali
    assert "def _login_required_message(lang: str, kind: str" in kaynak
    for tip in ('"site":', '"social":', '"brand":'):
        assert tip in kaynak, f"login mesajinda {tip} karsiligi yok"
    # Cagrilar tipsiz kalmamali — tipsiz cagri notr metin dondurur ve
    # kullaniciyi yanlis akisa yonlendirir.
    for tip in ('"tr", "site"', '"tr", "social"', '"tr", "brand"'):
        assert f'_login_required_message(request.lang or {tip})' in kaynak, \
            f"login duvarindan {tip} gecirilmiyor"


def test_login_duvari_uc_uctada_kurulu():
    """Anonim tarama kapisi uc tarama ucunda da olmali; biri unutulursa o kanal
    sinirsiz bedava tarama kapisi olarak acik kalir."""
    kaynak = (_KOK / "main.py").read_text(encoding="utf-8")
    assert kaynak.count("_login_required_message(request.lang") == 3, \
        "tarama uclarindan birinde login duvari eksik ya da fazladan eklenmis"


def test_bakiye_kapilari_sabit_bedel_yazmaz():
    """Bakiye on-kontrolleri scan_costs'tan gelmeli, koda gomulu olmamali.

    2026-08-04: bedeller 5/10 -> 20/20/10 yapildi ama UC bakiye kapisi sabit
    sayida kaldi (main.py'de iki kez `< 5`, bir kez `< 10`). Sonuc: bakiyesi
    10 olan kullanici web tarama kapisini GECIYOR, sonra 20 dusulemiyor ve
    tarama yarida kaliyor — kullanici "tarama tamamlanamadi" goruyor.
    """
    kaynak = (_KOK / "main.py").read_text(encoding="utf-8")
    import re
    kotu = re.findall(r"get_credit_balance\([^)]*\)\s*<\s*\d+", kaynak)
    assert not kotu, f"bakiye kapisinda sabit bedel: {kotu}"


def test_brand_check_hatasi_dogru_store_a_yazilir():
    """run_brand_check_job hata yolunda jobs_store (WEB store'u) kullanilmamali.

    Kullanilirsa KeyError firlar ve ASIL hatayi maskeler: kullanici
    "insufficient_credits" yerine anlamsiz bir job_id hatasi gorur.
    """
    kaynak = (_KOK / "main.py").read_text(encoding="utf-8")
    bas = kaynak.index("async def run_brand_check_job")
    son = kaynak.index("async def ", bas + 10)
    govde = kaynak[bas:son]
    # yorum satirlari haric — aciklamada gecmesi serbest, KODDA gecmesi degil
    kod = "\n".join(l for l in govde.splitlines() if not l.strip().startswith("#"))
    assert "jobs_store[" not in kod, \
        "run_brand_check_job icinde jobs_store kullanilmis — brand_checks_store olmali"


# ── Premium = "ödeme yaptı", "bakiyesi var" DEĞİL (kör denetim 2026-08-04) ──

def test_bakiyesi_biten_premium_sinirsiz_tarama_yapamaz(monkeypatch):
    """KRITIK regresyon: check_is_premium yalniz `total_credits_purchased>0`e
    bakar, GUNCEL BAKIYEYI sorgulamaz. Kapi eskiden premium'u kosulsuz
    geciriyordu -> tek kucuk paket almis kullanici bakiyesi SIFIRLANDIKTAN
    sonra da sinirsiz bedava tarama cekebiliyordu (tarama basina ~$0.32).
    """
    _install(monkeypatch, premium=True, balance=0, account_used=1,
             device_state={"used": True, "other": False})
    allowed, info = asyncio.run(free_scan.free_scan_gate("u1", "devtok", scan_cost=20))
    assert not allowed, "bakiyesi biten premium hala sinirsiz tarama yapabiliyor"
    assert info["reason"] == "free_limit_reached"


def test_bakiyesi_yeten_premium_gecer(monkeypatch):
    """Bedeli karsilayabilen premium tavansiz gecmeye devam etmeli."""
    _install(monkeypatch, premium=True, balance=100)
    allowed, info = asyncio.run(free_scan.free_scan_gate("u1", "devtok", scan_cost=20))
    assert allowed and info["reason"] == "premium"


def test_bedelsiz_cagri_premium_davranisi_bozulmaz(monkeypatch):
    """scan_cost<=0 (bedelsiz yol) eski davranisi korumali."""
    _install(monkeypatch, premium=True, balance=0)
    allowed, info = asyncio.run(free_scan.free_scan_gate("u1", "devtok", scan_cost=0))
    assert allowed and info["reason"] == "premium"


def test_uclarda_premium_kapiyi_atlamiyor():
    """Endpoint'ler `not is_premium` ile kapiyi ATLAMAMALI — karari gate verir."""
    kaynak = (_KOK / "main.py").read_text(encoding="utf-8")
    import re
    kod = "\n".join(l for l in kaynak.splitlines() if not l.strip().startswith("#"))
    kotu = re.findall(r"not is_premium\d?\s+and\s+not _is_internal_scan\(http_request\):\s*\n\s*allowed", kod)
    assert not kotu, "bir uc premium'da free_scan_gate'i atliyor"
    assert "if not sc_premium:" not in kod, "sosyal uc premium'da kapiyi atliyor"


def test_ucretsiz_hak_basarida_yakilir_submitte_degil():
    """Hak SUBMIT'te yakılırsa, tarama çökünce kullanıcı hakkını kaybeder ve
    iade yolu YOK (mobilde DeviceCheck biti kalıcı — reinstall bile resetlemez).

    Kaynak taraması: uçlar record_free_scan'i background_tasks ile SUBMIT anında
    çağırmamalı; iş fonksiyonu BAŞARI noktasında yakmalı.
    """
    kaynak = (_KOK / "main.py").read_text(encoding="utf-8")
    kod = "\n".join(l for l in kaynak.splitlines() if not l.strip().startswith("#"))
    assert "add_task(record_free_scan" not in kod, \
        "hak submit aninda yakiliyor — tarama cokerse geri verilmiyor"
    # Uc basari noktasinin ucunde de yakma cagrisi olmali
    assert kod.count("await _hakki_yak(job_id)") == 3, \
        "hak yakma cagrisi eksik/fazla (web + marka-cache + marka-tam = 3)"


def test_rapor_linki_paylasilabilir_kalir():
    """Sahiplik kontrolü BİLİNÇLİ olarak yok (kurucu kararı 2026-08-04): rapor
    linki paylaşılabilir olsun isteniyor. Üzerine kurulu üç yüzey var: web
    ShareResult, mobil lib/share, viral skor kartı geoni.ai/s/<jobId>.

    Biri sonuç uçlarına sahiplik kontrolü eklerse bu üçü de kırılır — bu test
    kararı kilitler ve değişiklik bilinçli yapılmaya zorlar.
    """
    kaynak = (_KOK / "main.py").read_text(encoding="utf-8")
    assert "SAHIPLIK KONTROLU YOK — BILINCLI KARAR" in kaynak, \
        "karar belgesi silinmiş — paylaşılabilir link kuralı kayboldu"
