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

    async def sahte_dusum(user_id, amount, reason, reference_id=None):
        dusumler.append((user_id, amount, reason))
        return dusum_sonucu

    monkeypatch.setattr(db, "deduct_credits", sahte_dusum)
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
    assert kutu[0]["credits_spent"] == 5
    assert dusumler == [("u1", 5, "web_audit")]


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
    assert kutu[0]["credits_spent"] == 10
    assert dusumler == [("u1", 10, "person_check")]


# --- Düşüm BAŞARISIZ olursa (2026-07-29, aynı ailenin 3. üyesi) -------------
# `deduct_credits` False dönebiliyor: ön bakiye kontrolü ile bu nokta arasında
# eşzamanlı başka bir tarama bakiyeyi tüketirse ya da DB hata verirse. Satır
# düşümden ÖNCE yazıldığı için "5 kredi harcandı" demeye devam ediyordu —
# defterde karşılığı olmayan hayalî maliyet.

def test_dusum_basarisizsa_maliyet_sifirlanir(monkeypatch):
    kutu, dusumler, yamalar = _kur(monkeypatch, dusum_sonucu=False)
    asyncio.run(db.save_audit("job-fail", {"domain": "example.com"},
                              {"score": 80}, user_id="u1"))
    assert dusumler == [("u1", 5, "web_audit")], "düşüm denenmiş olmalı"
    assert yamalar == [("eq.job-fail", {"credits_spent": 0})], \
        "düşüm başarısızken satırdaki maliyet 0'a çekilmeli"


def test_dusum_basarisizsa_marka_maliyeti_sifirlanir(monkeypatch):
    kutu, dusumler, yamalar = _kur(monkeypatch, dusum_sonucu=False)
    asyncio.run(db.save_brand_check("job-bfail", {"type": "person", "name": "X"},
                                    {"score": 50}, user_id="u1"))
    assert dusumler == [("u1", 10, "person_check")]
    assert yamalar == [("eq.job-bfail", {"credits_spent": 0})]


def test_dusum_basariliysa_duzeltme_yapilmaz(monkeypatch):
    """Mutlu yolda gereksiz PATCH atılmamalı (fazladan DB turu yok)."""
    kutu, dusumler, yamalar = _kur(monkeypatch, dusum_sonucu=True)
    asyncio.run(db.save_audit("job-ok", {"domain": "example.com"},
                              {"score": 80}, user_id="u1"))
    assert kutu[0]["credits_spent"] == 5
    assert yamalar == []


def test_anonimde_duzeltme_yolu_hic_calismaz(monkeypatch):
    """user_id yoksa düşüm hiç denenmez -> düzeltme de olmamalı."""
    kutu, dusumler, yamalar = _kur(monkeypatch, dusum_sonucu=False)
    asyncio.run(db.save_audit("job-anon2", {"domain": "example.com"},
                              {"score": 80}, user_id=None))
    assert dusumler == []
    assert yamalar == []
