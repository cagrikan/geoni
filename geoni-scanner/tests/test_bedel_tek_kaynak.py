"""Ö2 (fonksiyonel denetim 2026-08-12): private bedel ÖN-KONTROL = FİİLİ DÜŞÜM.

NE OLDU: run_brand_check_job ön-kontrolü tipe göre bedel seçiyordu
(social → 10) ama fiili düşüm sabit BRAND_SCAN_COST (20) yazıyordu.
`{social:true, private:true}` gönderen istemci ya 10-19 bakiyeyle kapıyı geçip
düşümde takılıyordu (tarama koşmuş, GEONI maliyeti oluşmuş, sonuç teslim
edilmemiş) ya da yarı fiyatlı hizmete 20 ödüyordu.

İKİ KAPAK:
  1. Bedel TEK kaynaktan: scan_costs.bedel_sec — ön-kontrol ve düşüm aynı
     değeri okur.
  2. `social+private` kombinasyonu şemada kapatıldı (422): resmi sosyal uç
     private=False sabitler, bu kombinasyonun meşru akışı yok — bedel/kapı
     tutarsızlıklarının tek giriş kapısıydı.
"""
import asyncio
import uuid

import pytest

from scan_costs import (BRAND_SCAN_COST, SOCIAL_SCAN_COST, WEB_SCAN_COST,
                        bedel_sec)


# ── Birim: bedel_sec (fastapi gerektirmez, her ortamda koşar) ───────────────

def test_bedel_sec_tip_tablosu():
    assert bedel_sec("person") == BRAND_SCAN_COST
    assert bedel_sec("brand") == BRAND_SCAN_COST
    assert bedel_sec("social") == SOCIAL_SCAN_COST
    assert bedel_sec("web") == WEB_SCAN_COST


def test_bedel_sec_social_bayragi_tipten_bagimsiz():
    """`type` serbest metin, `social` ayrı bayrak — ikisinden biri sosyalse
    sosyal tarife (tip 'person' yazsa bile)."""
    assert bedel_sec("person", social=True) == SOCIAL_SCAN_COST
    assert bedel_sec("social", social=False) == SOCIAL_SCAN_COST


def test_bedel_sec_bilinmeyen_tip_GUVENLI_tarafa_duser():
    """🪤 İstemciden gelen çöp tip asla 0/ucuz tarifeye düşmemeli."""
    assert bedel_sec("garbage") == BRAND_SCAN_COST
    assert bedel_sec(None) == BRAND_SCAN_COST
    assert bedel_sec("") == BRAND_SCAN_COST


# ── Şema: social+private kapalı ─────────────────────────────────────────────

def test_social_private_kombinasyonu_422(monkeypatch):
    pytest.importorskip("fastapi", reason="deploy kapısının minimal ortamında yok")
    import main
    with pytest.raises(Exception) as ei:
        main.BrandCheckRequest(name="Test Kisi", social=True, private=True)
    assert "social ve private" in str(ei.value)
    # Resmi akışlar kırılmaz:
    main.BrandCheckRequest(name="Test Kisi", social=True, private=False)
    main.BrandCheckRequest(name="Test Kisi", social=False, private=True)


# ── Davranışsal: private düşüm ön-kontrolle AYNI bedeli kullanır ────────────

def _calistir_private(monkeypatch, *, tip, bakiye):
    """run_brand_check_job'u sahte pipeline ile koşturur; düşüm kayıtlarını döner."""
    import main

    kayit = {"deduct": []}
    jid = str(uuid.uuid4())

    async def _uid(token):
        return "u1"

    async def _bakiye(uid):
        return bakiye

    async def _recall(**kw):
        return {"checked": True, "score": 55, "score_breakdown": {}}

    async def _stab(tip_, ad, skor, kirilim):
        return {}

    async def _deduct(uid, amount, desc, ref=None):
        kayit["deduct"].append((amount, desc))
        return True

    async def _adres(email, uid):
        return email

    async def _mail(adres, ad, sonuc, lang="tr"):
        return True

    monkeypatch.setattr(main, "get_user_id_from_token", _uid)
    monkeypatch.setattr(main, "get_credit_balance", _bakiye)
    monkeypatch.setattr(main, "check_brand_recall", _recall)
    monkeypatch.setattr(main, "build_stability", _stab)
    monkeypatch.setattr(main, "build_brand_payload",
                        lambda *a, **kw: {"score": 55})
    monkeypatch.setattr(main, "deduct_credits", _deduct)
    monkeypatch.setattr(main, "rapor_adresi", _adres)
    monkeypatch.setattr(main, "send_brand_report_email", _mail)
    monkeypatch.setattr(main, "sqs_enabled", lambda: False)
    monkeypatch.setattr(main.job_store, "yaz", lambda job_id, veri: None)

    req = main.BrandCheckRequest(name="Test Hedef", type=tip, private=True,
                                 email="a@b.com")
    main.brand_checks_store[jid] = {"job_id": jid, "status": "queued",
                                    "name": req.name, "topic": "", "result": None,
                                    "error": None, "created_at": ""}
    try:
        asyncio.run(main.run_brand_check_job(jid, req, token="tok"))
        return kayit, dict(main.brand_checks_store[jid])
    finally:
        main.brand_checks_store.pop(jid, None)


def test_private_social_tipinde_dusum_10(monkeypatch):
    """🔴 Asıl dava: bakiye 12 (10-19 aralığı) → ön-kontrol GEÇER ve düşüm de
    AYNI bedeli (10) kullanır. Eski kodda düşüm 20 istediği için burada
    'failed/insufficient_credits' üretirdi — tarama boşa koşmuş olurdu."""
    pytest.importorskip("fastapi", reason="deploy kapısının minimal ortamında yok")
    kayit, is_kaydi = _calistir_private(monkeypatch, tip="social", bakiye=12)
    assert is_kaydi["status"] == "complete", \
        f"ön-kontrol/düşüm bedeli uyuşmuyor: {is_kaydi.get('error')}"
    assert kayit["deduct"] == [(SOCIAL_SCAN_COST, "social_check_private")]


def test_private_person_tipinde_dusum_20(monkeypatch):
    pytest.importorskip("fastapi", reason="deploy kapısının minimal ortamında yok")
    kayit, is_kaydi = _calistir_private(monkeypatch, tip="person", bakiye=25)
    assert is_kaydi["status"] == "complete"
    assert kayit["deduct"] == [(BRAND_SCAN_COST, "person_check_private")]


def test_private_social_bakiye_yetmiyorsa_TARAMA_KOSMAZ(monkeypatch):
    """Bakiye 9 < 10: kapı reddeder, pahalı pipeline hiç başlamaz."""
    pytest.importorskip("fastapi", reason="deploy kapısının minimal ortamında yok")
    kayit, is_kaydi = _calistir_private(monkeypatch, tip="social", bakiye=9)
    assert is_kaydi["status"] == "failed"
    assert is_kaydi["error"] == "insufficient_credits"
    assert kayit["deduct"] == []
