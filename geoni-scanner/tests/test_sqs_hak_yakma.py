"""K1 (fonksiyonel denetim 2026-08-12): SQS modunda ücretsiz hak GERÇEKTEN yakılır.

NE OLDU: `_bekleyen_hak` süreç-yerel bir sözlük. API (App Runner) submit'te
kendi sözlüğüne yazıyordu; iş SQS üzerinden WORKER sürecine (ECS) gidiyordu ve
oradaki sözlük BOŞ olduğu için `_hakki_yak` sessiz no-op'tu. Sonuç:
`record_free_scan` web taramaları için ASLA çağrılmıyordu —
`profiles.free_scans_used` artmıyor, DeviceCheck biti işaretlenmiyor, bakiyesi
0 kullanıcı sınırsız bedava tarama çekebiliyordu (tarama başına ~$0,32).

Eski test (`test_free_scan.py`) yalnız kaynakta `await _hakki_yak(job_id)`
sayıyordu; süreç ayrımını göremediği için YEŞİLDİ. Bu dosya tam o kör noktayı
DAVRANIŞSAL kapatır: `worker.process_message` GERÇEK `main.run_audit_job`u
koşturur (pipeline adımları sahte, akış gerçek) ve yakma çağrısını doğrular.

🪤 `main`/`worker` MODÜL SEVİYESİNDE import edilmez (deploy kapısının minimal
ortamında fastapi/boto3 yok; bkz. test_dependency_surface). `_moduller()`
testin içinde importorskip ile yükler.
"""
import asyncio
import json
import os
import uuid

import pytest

GATE = {"reason": "ok", "device_state": {"used": False, "other": False},
        "account_used": 0}


def _moduller():
    """main + worker'ı testin İÇİNDE yükler; minimal ortamda testi atlar."""
    pytest.importorskip("fastapi", reason="deploy kapısının minimal ortamında yok")
    pytest.importorskip("boto3", reason="deploy kapısının minimal ortamında yok")
    # worker.py import anında SCAN_QUEUE_URL ister; boto3.client offline çalışır.
    os.environ.setdefault(
        "SCAN_QUEUE_URL",
        "https://sqs.eu-central-1.amazonaws.com/000000000000/test-kuyruk")
    import main
    import worker
    return main, worker


def _pipeline_stub(monkeypatch, main, *, crawl_patlasin=False):
    """run_audit_job'un ağa çıkan her adımını sahteler; AKIŞ gerçek kalır.
    Dönen sözlük çağrı kayıtlarını taşır."""
    kayit = {"record": [], "save": [], "update": []}

    async def _crawl(domain, page_limit):
        if crawl_patlasin:
            raise RuntimeError("crawl patladı (senaryo)")
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
        return {"score": 70, "domain": request.domain}

    async def _save(job_id, req, res, uid):
        kayit["save"].append(job_id)
        return True

    async def _mail(email, domain, payload, lang="tr"):
        return True

    async def _uid(token):
        return "u-token" if token else None

    async def _update(job_id, status, result=None, score=None, error=None):
        kayit["update"].append((status, error))
        return True

    async def _record(uid, dev, info):
        kayit["record"].append((uid, dev, info))

    monkeypatch.setattr(main, "crawl_domain", _crawl)
    monkeypatch.setattr(main, "check_indexing_status", _index)
    monkeypatch.setattr(main, "infer_brand_identity", _identity)
    monkeypatch.setattr(main, "generate_topics_and_opportunities", _topics)
    monkeypatch.setattr(main, "check_brand_recall", _recall)
    monkeypatch.setattr(main, "compute_ai_visibility_score", _score)
    monkeypatch.setattr(main, "_build_audit_result_payload", _payload)
    monkeypatch.setattr(main, "save_audit", _save)
    monkeypatch.setattr(main, "send_audit_report_email", _mail)
    monkeypatch.setattr(main, "get_user_id_from_token", _uid)
    monkeypatch.setattr(main, "update_audit_status", _update)
    monkeypatch.setattr(main, "record_free_scan", _record)
    monkeypatch.setattr(main, "sqs_enabled", lambda: True)
    return kayit


def _sqs_stub(monkeypatch, worker):
    """worker'ın SQS istemcisini sahteler; silinen mesajları kaydeder."""
    silinen = []

    class SahteSqs:
        def delete_message(self, QueueUrl=None, ReceiptHandle=None):
            silinen.append(ReceiptHandle)

        def change_message_visibility(self, **kw):
            pass

    monkeypatch.setattr(worker, "sqs", SahteSqs())
    return silinen


def _mesaj(job_id, hak=GATE):
    return json.dumps({
        "kind": "web_audit", "job_id": job_id,
        "request": {"domain": "ornek.com", "email": "a@b.com"},
        "token": "", "ic_dogrulama": False,
        "hak": ({"user_id": "u1", "device_token": "dev1", "gate_info": hak}
                if hak else None),
    })


def test_worker_mesajdaki_hak_ile_YAKAR(monkeypatch):
    """🔴 Asıl dava: mesaj `hak` taşıyorsa başarılı tarama record_free_scan'i
    MESAJDAKİ kullanıcı/cihaz/gate bilgisiyle çağırır."""
    main, worker = _moduller()
    kayit = _pipeline_stub(monkeypatch, main)
    silinen = _sqs_stub(monkeypatch, worker)
    jid = str(uuid.uuid4())

    asyncio.run(worker.process_message(_mesaj(jid), "rcpt-1"))

    assert kayit["record"] == [("u1", "dev1", GATE)], \
        "hak yakılmadı — K1 geri geldi (worker sözlüğü boş no-op)"
    assert silinen == ["rcpt-1"]                       # iş bitti, mesaj silindi
    assert jid not in main._bekleyen_hak               # sözlük sızdırmıyor
    assert jid not in main.jobs_store


def test_worker_hak_TASIMAYAN_mesajda_yakmaz(monkeypatch):
    """private/premium/paid yol: mesajda hak yok → record_free_scan çağrılmaz."""
    main, worker = _moduller()
    kayit = _pipeline_stub(monkeypatch, main)
    _sqs_stub(monkeypatch, worker)
    jid = str(uuid.uuid4())

    asyncio.run(worker.process_message(_mesaj(jid, hak=None), "rcpt-2"))

    assert kayit["record"] == []


def test_basarisiz_taramada_hak_YAKILMAZ_ama_sizmaz(monkeypatch):
    """Kurucu kuralı: hak yalnız BAŞARILI taramada gider. Çöken taramada
    record çağrılmaz (kullanıcının hakkı durur) ama girdi de sözlükte
    kalmaz (bellek sızıntısı — K1 yan hasarı)."""
    main, worker = _moduller()
    kayit = _pipeline_stub(monkeypatch, main, crawl_patlasin=True)
    silinen = _sqs_stub(monkeypatch, worker)
    jid = str(uuid.uuid4())

    asyncio.run(worker.process_message(_mesaj(jid), "rcpt-3"))

    assert kayit["record"] == [], "çöken taramada hak yakıldı — kural ihlali"
    assert jid not in main._bekleyen_hak, "başarısız işin hak girdisi sızıyor"
    # uygulama-içi hata deterministik: mesaj yine silinir (retry para yakar)
    assert silinen == ["rcpt-3"]
    assert ("failed", "RuntimeError: crawl patladı (senaryo)") in kayit["update"]


def test_api_enqueue_mesaja_hak_KOYAR_ve_kendi_sozlugunu_temizler(monkeypatch):
    """Zincirin API ucu: ücretsiz-hak kapısından geçen submit, SQS mesajına
    `hak` alanını koymalı ve girdiyi KENDİ sözlüğünden düşmeli (oradaki kopya
    asla tüketilmiyordu → sınırsız büyüme)."""
    main, _ = _moduller()
    from fastapi import BackgroundTasks
    from starlette.requests import Request

    yakalanan = {}

    async def _enqueue(payload):
        yakalanan.update(payload)

    async def _gate(uid, dev, scan_cost=0):
        return True, dict(GATE)

    async def _pending(job_id, tip, domain, uid):
        return True

    async def _uid(token):
        return "u1"

    async def _hayir(uid):
        return False

    async def _noop(*a, **kw):
        return None

    def _sync_noop(*a, **kw):
        return None

    monkeypatch.setattr(main, "sqs_enabled", lambda: True)
    monkeypatch.setattr(main, "enqueue_scan", _enqueue)
    monkeypatch.setattr(main, "free_scan_gate", _gate)
    monkeypatch.setattr(main, "create_pending_audit", _pending)
    monkeypatch.setattr(main, "get_user_id_from_token", _uid)
    monkeypatch.setattr(main, "is_user_suspended", _hayir)
    monkeypatch.setattr(main, "check_is_premium", _hayir)
    monkeypatch.setattr(main, "enforce_audit_rate_limits", _sync_noop)
    monkeypatch.setattr(main, "enforce_turnstile", _noop)
    monkeypatch.setattr(main, "assert_public_host", _sync_noop)   # DNS'e çıkma
    monkeypatch.setattr(main, "host_cozuluyor_mu", lambda d: True)

    req = main.AuditRequest(domain="ornek.com", email="a@b.com",
                            device_token="dev1")
    http_req = Request({"type": "http", "method": "POST", "path": "/api/audit/quick",
                        "headers": [(b"authorization", b"Bearer tok")],
                        "client": ("203.0.113.9", 1234), "query_string": b""})

    yanit = asyncio.run(main.start_audit(req, BackgroundTasks(), http_req))

    assert yanit.status == "queued"
    hak = yakalanan.get("hak")
    assert hak is not None, "SQS mesajı hak taşımıyor — worker yakamaz (K1)"
    assert hak["user_id"] == "u1" and hak["device_token"] == "dev1"
    assert hak["gate_info"]["reason"] == "ok"
    # API kendi sözlüğünde girdi bırakmaz (sızıntı kapandı)
    assert yanit.job_id not in main._bekleyen_hak
