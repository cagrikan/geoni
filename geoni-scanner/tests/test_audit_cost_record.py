"""Kaydedilen maliyet GERÇEK düşümle aynı olmalı (2026-07-29 canlı bulgu).

Android'den gelen anonim ücretsiz tarama (4efadb05…, user_id=None) satıra
`credits_spent: 5` yazmıştı, oysa `deduct_credits` yalnız `if user_id and deduct`
koşulunda çalıştığı için kimseden kredi düşmemişti. Admin raporu (db.py:2643,
sıralama db.py:2523) bu satırları harcanmış kredi sayıyordu.

Kilitlenen davranış: yazılan maliyet ile düşümün koşulu AYNI — user_id yoksa 0,
ve düşüm fonksiyonu hiç çağrılmaz.
"""
import asyncio

import db
from scan_costs import WEB_SCAN_COST, BRAND_SCAN_COST


class FakeResp:
    def __init__(self, status_code=201, json_data=None):
        self.status_code = status_code
        self._json = [] if json_data is None else json_data

    def json(self):
        return self._json

    @property
    def text(self):
        return ""


class FakeClient:
    """POST gövdesini yakalar — audits satırına ne yazıldığını görmek için.
    PATCH'leri de ayrı yakalar: düşüm başarısızsa maliyetin düzeltilmesi
    gerekiyor ve bu düzeltme bir PATCH ile yapılıyor."""

    def __init__(self, kutu, yamalar):
        self._kutu = kutu
        self._yamalar = yamalar

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        if "/audits" in url:
            self._kutu.append(kw.get("json") or {})
        return FakeResp(201)

    async def patch(self, url, **kw):
        if "/audits" in url:
            self._yamalar.append(((kw.get("params") or {}).get("id"), kw.get("json") or {}))
        return FakeResp(204)


def _kur(monkeypatch, dusum_sonucu=True):
    kutu, dusumler, yamalar = [], [], []
    monkeypatch.setattr(db, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(db, "SUPABASE_SERVICE_KEY", "svc")
    monkeypatch.setattr(db.httpx, "AsyncClient", lambda *a, **k: FakeClient(kutu, yamalar))

    # 2026-08-08: kayit yolu artik `deduct_credits_detayli` cagiriyor — sebebi de
    # doner (bakiye yetmemesi ucretsiz-hak yolunda BEKLENEN, log seviyesi ona gore).
    # Sahte yalniz eski adi yamalasaydi gercek fonksiyon kosardi ve test sessizce
    # baska bir seyi olcerdi.
    async def sahte_dusum(user_id, amount, reason, reference_id=None):
        dusumler.append((user_id, amount, reason))
        return dusum_sonucu, ("dusuldu" if dusum_sonucu else "insufficient")

    monkeypatch.setattr(db, "deduct_credits_detayli", sahte_dusum)
    # Yan etkiler (retention / referans odulu) bu testin konusu degil.
    monkeypatch.setattr(db, "run_audit_retention", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(db, "grant_referral_reward", lambda *a, **k: asyncio.sleep(0))
    return kutu, dusumler, yamalar


def test_anonim_tarama_maliyet_yazmaz(monkeypatch):
    """user_id=None: düşüm yok -> credits_spent de 0 olmalı (regresyon)."""
    kutu, dusumler, _y = _kur(monkeypatch)
    asyncio.run(db.save_audit("job-anon", {"domain": "example.com"},
                              {"score": 80}, user_id=None))
    assert kutu, "audits satırı yazılmadı"
    assert kutu[0]["credits_spent"] == 0
    assert dusumler == [], "anonim taramada kredi düşülmemeli"


def test_girisli_tarama_maliyet_yazar(monkeypatch):
    """user_id var + deduct: 5 yazılır ve gerçekten 5 düşülür."""
    kutu, dusumler, _y = _kur(monkeypatch)
    asyncio.run(db.save_audit("job-user", {"domain": "example.com"},
                              {"score": 80}, user_id="u1"))
    assert kutu[0]["credits_spent"] == WEB_SCAN_COST
    assert dusumler == [("u1", WEB_SCAN_COST, "web_audit")]


def test_izleme_taramasi_ucretsiz(monkeypatch):
    """deduct=False (otomatik izleme): user_id olsa bile 0 ve düşüm yok."""
    kutu, dusumler, _y = _kur(monkeypatch)
    asyncio.run(db.save_audit("job-mon", {"domain": "example.com"},
                              {"score": 80}, user_id="u1", deduct=False))
    assert kutu[0]["credits_spent"] == 0
    assert dusumler == []


def test_anonim_marka_kontrolu_maliyet_yazmaz(monkeypatch):
    """save_brand_check aynı kurala uymalı (10 kredi tarafı)."""
    kutu, dusumler, _y = _kur(monkeypatch)
    asyncio.run(db.save_brand_check("job-brand-anon", {"type": "person", "name": "X"},
                                    {"score": 50}, user_id=None))
    assert kutu[0]["credits_spent"] == 0
    assert dusumler == []


def test_girisli_marka_kontrolu_maliyet_yazar(monkeypatch):
    kutu, dusumler, _y = _kur(monkeypatch)
    asyncio.run(db.save_brand_check("job-brand", {"type": "person", "name": "X"},
                                    {"score": 50}, user_id="u1"))
    assert kutu[0]["credits_spent"] == BRAND_SCAN_COST
    assert dusumler == [("u1", BRAND_SCAN_COST, "person_check")]


# --- Düşüm BAŞARISIZ olursa (2026-07-29, aynı ailenin 3. üyesi) -------------
# `deduct_credits` False dönebiliyor: ön bakiye kontrolü ile bu nokta arasında
# eşzamanlı başka bir tarama bakiyeyi tüketirse ya da DB hata verirse. Satır
# düşümden ÖNCE yazıldığı için "5 kredi harcandı" demeye devam ediyordu —
# defterde karşılığı olmayan hayalî maliyet.

def test_dusum_basarisizsa_maliyet_sifirlanir(monkeypatch):
    kutu, dusumler, yamalar = _kur(monkeypatch, dusum_sonucu=False)
    asyncio.run(db.save_audit("job-fail", {"domain": "example.com"},
                              {"score": 80}, user_id="u1"))
    assert dusumler == [("u1", WEB_SCAN_COST, "web_audit")], "düşüm denenmiş olmalı"
    assert yamalar == [("eq.job-fail", {"credits_spent": 0})], \
        "düşüm başarısızken satırdaki maliyet 0'a çekilmeli"


def test_dusum_basarisizsa_marka_maliyeti_sifirlanir(monkeypatch):
    kutu, dusumler, yamalar = _kur(monkeypatch, dusum_sonucu=False)
    asyncio.run(db.save_brand_check("job-bfail", {"type": "person", "name": "X"},
                                    {"score": 50}, user_id="u1"))
    assert dusumler == [("u1", BRAND_SCAN_COST, "person_check")]
    assert yamalar == [("eq.job-bfail", {"credits_spent": 0})]


def test_dusum_basariliysa_duzeltme_yapilmaz(monkeypatch):
    """Mutlu yolda gereksiz PATCH atılmamalı (fazladan DB turu yok)."""
    kutu, dusumler, yamalar = _kur(monkeypatch, dusum_sonucu=True)
    asyncio.run(db.save_audit("job-ok", {"domain": "example.com"},
                              {"score": 80}, user_id="u1"))
    assert kutu[0]["credits_spent"] == WEB_SCAN_COST
    assert yamalar == []


def test_anonimde_duzeltme_yolu_hic_calismaz(monkeypatch):
    """user_id yoksa düşüm hiç denenmez -> düzeltme de olmamalı."""
    kutu, dusumler, yamalar = _kur(monkeypatch, dusum_sonucu=False)
    asyncio.run(db.save_audit("job-anon2", {"domain": "example.com"},
                              {"score": 80}, user_id=None))
    assert dusumler == []
    assert yamalar == []


# --- Süre ölçümü (K3, 2026-07-29) ------------------------------------------
# `audits` satırı kişi/marka/sosyal taramada işin SONUNDA oluşuyor; created_at
# DB varsayılanına (now()) bırakılınca bitiş anını yazıyor ve
# `completed_at - created_at` NEGATİF çıkıyordu. Ölçüm: social 54/54,
# person 20/20, brand 2/2 negatif. Web etkilenmiyor (SQS 'queued' satırı önce).

def test_baslangic_verilirse_created_at_yazilir(monkeypatch):
    kutu, _d, _y = _kur(monkeypatch)
    asyncio.run(db.save_brand_check("job-sure", {"type": "person", "name": "X"},
                                    {"score": 50, "created_at": "2026-07-29T21:05:00+00:00"},
                                    user_id="u1",
                                    started_at="2026-07-29T21:00:00+00:00"))
    satir = kutu[0]
    assert satir["created_at"] == "2026-07-29T21:00:00+00:00"
    assert satir["completed_at"] == "2026-07-29T21:05:00+00:00"
    assert satir["created_at"] < satir["completed_at"], "süre negatif olmamalı"


def test_baslangic_verilmezse_created_at_yazilmaz(monkeypatch):
    """Geriye dönük uyumluluk: eski çağıranlar bozulmamalı (DB varsayılanı)."""
    kutu, _d, _y = _kur(monkeypatch)
    asyncio.run(db.save_brand_check("job-eski", {"type": "person", "name": "X"},
                                    {"score": 50}, user_id="u1"))
    assert "created_at" not in kutu[0]


# ── Sentry gurultusu: BEKLENEN dusum basarisizligi ERROR degildir ────────
# 🔴 YASANDI (2026-08-08): Sentry'de "kontor dusumu BASARISIZ" satirini gercek
# bir kusur sanip kovaladim; sonunda kendi test hesabim cikti. Vakalarin cogu
# BEKLENEN: ucretsiz hakla tarama yapan kullanicinin bakiyesi zaten 0, dusum
# elbette tutmuyor, satir 0'a cekiliyor ve kullanici bedava taramasini aliyor —
# tam olmasi gereken sey. Her durumu ERROR basmak, Sentry'yi kurt masaliyla
# doldurup GERCEK yaris kosullarini gorunmez yapiyordu.
KAYNAK_DB = (Path(__file__).resolve().parent.parent / "db.py").read_text(encoding="utf-8") \
    if "Path" in dir() else None


def _db_kaynak():
    from pathlib import Path as _P
    return (_P(__file__).resolve().parent.parent / "db.py").read_text(encoding="utf-8")


def test_bakiye_yetmemesi_ERROR_basmaz():
    s = _db_kaynak()
    i = s.index("async def _maliyeti_sifirla")
    govde = s[i:i + 2600]
    assert "logger.info if beklenen else logger.error" in govde, \
        "seviye sebebe gore secilmiyor — her durum ERROR"
    assert '"insufficient"' in govde


def test_DIGER_sebepler_hala_ERROR():
    """🪤 Gurultuyu kismarken gercek kusuru da susturmayalim: HTTP hatasi,
    istisna, yapilandirma eksigi -> yaris kosulu ya da DB hatasi demektir."""
    s = _db_kaynak()
    i = s.index("async def _maliyeti_sifirla")
    govde = s[i:i + 2600]
    assert "else logger.error" in govde


def test_sebep_LOGA_yaziliyor():
    """Sebep gorunmezse bir sonraki sefer yine tahmin yurutulur."""
    s = _db_kaynak()
    i = s.index("async def _maliyeti_sifirla")
    assert "sebep=%s" in s[i:i + 2600]


def test_eski_imza_KORUNDU():
    """`deduct_credits` bool donmeye devam etmeli — private yollar onu cagiriyor."""
    s = _db_kaynak()
    i = s.index("async def deduct_credits(")
    govde = s[i:i + 500]
    assert "-> bool:" in govde
    assert "ok, _ = await deduct_credits_detayli(" in govde
