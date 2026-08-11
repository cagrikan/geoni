"""Ö1 (fonksiyonel denetim 2026-08-12): private sonuç teslim edilemezse İADE.

NE OLDU: private web taramasında sıra "düş → sonucu satıra yaz" ve yazım
(update_audit_status) hatayı SESSİZCE yutuyordu; worker da bellekteki sonucu
pop ediyor. PATCH düşerse kullanıcı 20 kredi ödemiş, rapor hiçbir yerde yok —
ve tarama yolunda iade fonksiyonu yoktu.

KAPAK:
  1. db.update_audit_status artık bool döner (2xx=True; HTTP hatası/istisna=False).
  2. Yazım False dönerse main.run_audit_job `tarama_bedelini_iade_et` çağırır —
     apply_credit_change RPC'si, external_id=scan_refund_<job_id> ile İDEMPOTENT.
  3. Sıra bilerek "düş → yaz → tutmadıysa TELAFİ" kaldı; "önce yaz sonra düş"
     düşüm hatasında ÜCRETSİZ rapor demek olurdu (güvenlik O2 sınıfı).
"""
import asyncio
import uuid

import pytest

import db


# ── db.update_audit_status: dönüş sözleşmesi ────────────────────────────────

class _Yanit:
    def __init__(self, kod, text=""):
        self.status_code = kod
        self.text = text


class _Istemci:
    """httpx.AsyncClient sahtesi — PATCH/POST kaydeder, ayarlı yanıtı döner."""
    yanit_kodu = 204
    patlasin = False
    cagrilar = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def patch(self, url, headers=None, params=None, json=None, timeout=None):
        if _Istemci.patlasin:
            raise RuntimeError("ağ koptu")
        _Istemci.cagrilar.append(("patch", url, json))
        return _Yanit(_Istemci.yanit_kodu)

    async def post(self, url, headers=None, json=None, timeout=None):
        if _Istemci.patlasin:
            raise RuntimeError("ağ koptu")
        _Istemci.cagrilar.append(("post", url, json))
        return _Yanit(_Istemci.yanit_kodu, "")


def _kur_db(monkeypatch, *, kod=204, patlasin=False):
    monkeypatch.setattr(db, "SUPABASE_URL", "https://ornek.supabase.co")
    monkeypatch.setattr(db, "SUPABASE_SERVICE_KEY", "test-anahtar")
    monkeypatch.setattr(db.httpx, "AsyncClient", _Istemci)
    _Istemci.yanit_kodu = kod
    _Istemci.patlasin = patlasin
    _Istemci.cagrilar = []


def test_update_audit_status_2xx_True(monkeypatch):
    _kur_db(monkeypatch, kod=204)
    assert asyncio.run(db.update_audit_status("j1", "complete",
                                              result={"score": 70})) is True


def test_update_audit_status_http_hatasi_False(monkeypatch):
    """🔴 Eskiden hata yutulup None dönüyordu — çağıran teslimin düştüğünü
    ASLA öğrenemiyordu."""
    _kur_db(monkeypatch, kod=500)
    assert asyncio.run(db.update_audit_status("j2", "complete",
                                              result={"score": 70})) is False


def test_update_audit_status_istisna_False_ve_TARAMAYI_DUSURMEZ(monkeypatch):
    _kur_db(monkeypatch, patlasin=True)
    # raise etmez (durum güncellemesi taramayı asla düşürmemeli), False döner
    assert asyncio.run(db.update_audit_status("j3", "crawling")) is False


# ── db.tarama_bedelini_iade_et: idempotent telafi ───────────────────────────

class _RpcYanit(_Yanit):
    govde = {"applied": True}

    def json(self):
        return _RpcYanit.govde


class _RpcIstemci(_Istemci):
    async def post(self, url, headers=None, json=None, timeout=None):
        _Istemci.cagrilar.append(("post", url, json))
        return _RpcYanit(_Istemci.yanit_kodu)


def _kur_rpc(monkeypatch, *, kod=200, govde=None):
    _kur_db(monkeypatch, kod=kod)
    monkeypatch.setattr(db.httpx, "AsyncClient", _RpcIstemci)
    _RpcYanit.govde = govde if govde is not None else {"applied": True}


def test_iade_apply_credit_change_ile_ledgerli_ve_idempotent(monkeypatch):
    _kur_rpc(monkeypatch)
    ok = asyncio.run(db.tarama_bedelini_iade_et("u1", 20, "job-1", "sonuc_yazilamadi"))
    assert ok is True
    (_, url, govde), = _Istemci.cagrilar
    assert url.endswith("/rpc/apply_credit_change"), \
        "iade ledger'sız bakiye patch'ine dönmüş — defter tutmaz"
    assert govde["p_amount"] == 20
    assert govde["p_external_id"] == "scan_refund_job-1", \
        "external_id job'a bağlı değil — çifte iade kapısı açık"
    assert govde["p_idempotent"] is True


def test_iade_duplicate_da_BASARI_sayilir(monkeypatch):
    """SQS yeniden teslimi aynı işi iki kez koşturabilir; ikinci iade
    'duplicate' döner ve bu bir hata DEĞİLDİR (tek iade yazıldı)."""
    _kur_rpc(monkeypatch, govde={"applied": False, "reason": "duplicate"})
    assert asyncio.run(db.tarama_bedelini_iade_et("u1", 20, "job-1", "x")) is True


def test_iade_http_hatasi_False(monkeypatch):
    _kur_rpc(monkeypatch, kod=500)
    assert asyncio.run(db.tarama_bedelini_iade_et("u1", 20, "job-1", "x")) is False


def test_iade_gecersiz_girdide_rpc_cagirmaz(monkeypatch):
    _kur_rpc(monkeypatch)
    assert asyncio.run(db.tarama_bedelini_iade_et(None, 20, "j", "x")) is False
    assert asyncio.run(db.tarama_bedelini_iade_et("u1", 0, "j", "x")) is False
    assert _Istemci.cagrilar == []


# ── main.run_audit_job private yolu: yazım düşerse iade tetiklenir ──────────

@pytest.fixture
def ana(monkeypatch):
    pytest.importorskip("fastapi", reason="deploy kapısının minimal ortamında yok")
    import main
    return main


def _private_kos(monkeypatch, main, *, yazim_sonuclari):
    """run_audit_job'u private modda koşturur. yazim_sonuclari: update_audit_status
    çağrılarının sırayla dönüşleri (durum yazımları + complete yazımı)."""
    kayit = {"iade": [], "update": []}
    jid = str(uuid.uuid4())
    donusler = list(yazim_sonuclari)

    async def _crawl(domain, page_limit):
        return {"total_pages": 3, "pages": [], "domain": domain}

    async def _index(pages, domain=None):
        return {}

    async def _identity(domain, pages, lang):
        return {"name": "X", "topic": "t"}

    async def _topics(domain, pages, lang):
        return {"performing_topics": [], "opportunity_topics": []}

    async def _recall(name, **kw):
        return {"checked": True, "score": 60, "name": name, "web_results": []}

    async def _score(c, i, b=None):
        return {"overall_score": 70}

    async def _payload(request, *a, **kw):
        return {"score": 70}

    async def _uid(token):
        return "u1"

    async def _bakiye(uid):
        return 100

    async def _deduct(uid, amount, desc, ref=None):
        return True

    async def _update(job_id, status, result=None, score=None, error=None):
        kayit["update"].append((status, error))
        return donusler.pop(0) if donusler else True

    async def _iade(uid, amount, job_id, sebep):
        kayit["iade"].append((uid, amount, job_id, sebep))
        return True

    async def _mail(email, domain, payload, lang="tr"):
        return True

    monkeypatch.setattr(main, "crawl_domain", _crawl)
    monkeypatch.setattr(main, "check_indexing_status", _index)
    monkeypatch.setattr(main, "infer_brand_identity", _identity)
    monkeypatch.setattr(main, "generate_topics_and_opportunities", _topics)
    monkeypatch.setattr(main, "check_brand_recall", _recall)
    monkeypatch.setattr(main, "compute_ai_visibility_score", _score)
    monkeypatch.setattr(main, "_build_audit_result_payload", _payload)
    monkeypatch.setattr(main, "get_user_id_from_token", _uid)
    monkeypatch.setattr(main, "get_credit_balance", _bakiye)
    monkeypatch.setattr(main, "deduct_credits", _deduct)
    monkeypatch.setattr(main, "update_audit_status", _update)
    monkeypatch.setattr(main, "tarama_bedelini_iade_et", _iade)
    monkeypatch.setattr(main, "send_audit_report_email", _mail)
    monkeypatch.setattr(main, "sqs_enabled", lambda: True)

    req = main.AuditRequest(domain="ornek.com", email="a@b.com", private=True)
    main.jobs_store[jid] = {"job_id": jid, "status": "queued", "domain": req.domain,
                            "email": req.email, "created_at": "", "result": None,
                            "error": None}
    try:
        asyncio.run(main.run_audit_job(jid, req, token="tok"))
        return jid, kayit
    finally:
        main.jobs_store.pop(jid, None)


def test_yazim_duserse_IADE_edilir(monkeypatch, ana):
    """🔴 Asıl dava: complete yazımı False dönerse kredi geri verilir ve satır
    failed'a çekilmeye çalışılır (o da düşerse 20 dk terk kontrolü devreye girer).

    Yazım sırası: crawling(T), indexing(T), scoring(T), complete(F), failed(T)."""
    jid, kayit = _private_kos(monkeypatch, ana,
                              yazim_sonuclari=[True, True, True, False, True])
    from scan_costs import WEB_SCAN_COST
    assert kayit["iade"] == [("u1", WEB_SCAN_COST, jid, "sonuc_yazilamadi")], \
        "kredi düşüldü, sonuç kayboldu, iade YOK — Ö1 geri geldi"
    assert ("failed", "sonuc_teslim_edilemedi") in kayit["update"]


def test_yazim_tutarsa_iade_YOK(monkeypatch, ana):
    jid, kayit = _private_kos(monkeypatch, ana,
                              yazim_sonuclari=[True, True, True, True])
    assert kayit["iade"] == [], "başarılı teslimde iade yapılmaz (çifte bedava)"
    assert ("complete", None) in kayit["update"]
