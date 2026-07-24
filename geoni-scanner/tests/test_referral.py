"""Viral referral cekirdegi — Faz 1 (attribution, para vermez).
Sema HAZIR (migration yok): profiles.referral_code/referred_by. Kod deterministik
uuid-tureviyle carpisma/race'siz. Bu testler ag GEREKTIRMEYEN saf/guard mantigini
kilitler; DB'li yollar deploy sonrasi e2e dogrulanir."""
import asyncio
import uuid
import db


def test_ref_code_deterministic_and_format():
    u = "b993ae8f-1234-4abc-8def-0123456789ab"
    c = db._ref_code_for(u)
    assert c == db._ref_code_for(u)              # deterministik
    assert len(c) == 8 and c == c.lower() and c.isalnum()


def test_ref_code_no_collision_sample():
    codes = {db._ref_code_for(str(uuid.uuid4())) for _ in range(3000)}
    assert len(codes) == 3000                     # 3000 uuid -> 3000 benzersiz kod


def test_ref_code_handles_non_uuid():
    # uuid degilse cokmez, yine 8-kar kod uretir
    c = db._ref_code_for("not-a-uuid")
    assert len(c) == 8


def test_set_referred_by_missing_params():
    r = asyncio.run(db.set_referred_by("", "abcd1234"))
    assert r["ok"] is False


def test_set_referred_by_rejects_invalid_code(monkeypatch):
    # env guard'ini gec, gecersiz kod AG CAGRISI YAPMADAN reddedilmeli
    monkeypatch.setattr(db, "SUPABASE_URL", "http://x", raising=False)
    monkeypatch.setattr(db, "SUPABASE_SERVICE_KEY", "k", raising=False)
    r = asyncio.run(db.set_referred_by("user-1", "!!bad!!"))
    assert r["ok"] is False and r["reason"] == "gecersiz kod"
    r2 = asyncio.run(db.set_referred_by("user-1", "ab"))   # cok kisa
    assert r2["ok"] is False and r2["reason"] == "gecersiz kod"
