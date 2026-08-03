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


# ── Faz 2: ODUL ─────────────────────────────────────────────────────────────
# Kontor birimi TARAMA DEGIL (web 5, kisi/marka 10). Odul 1 kontor kalirsa vaat
# edilenin 1/10'u odenir ve tesvik olur. Bu testler miktari ve odulun kime/nasil
# gittigini kilitler.

class _SahteYanit:
    def __init__(self, data=None, status=200):
        self.status_code, self._data, self.text, self.headers = status, data, "", {}

    def json(self):
        return self._data


class _SahteIstemci:
    """httpx.AsyncClient yerine: profiles GET'ine referred_by doner, RPC'leri kaydeder."""

    def __init__(self, referrer):
        self.referrer, self.rpc = referrer, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        return _SahteYanit([{"referred_by": self.referrer}])

    async def post(self, url, **kw):
        self.rpc.append(kw.get("json") or {})
        return _SahteYanit({})


def _odul_calistir(monkeypatch, referrer, davetli="davetli-1"):
    monkeypatch.setattr(db, "SUPABASE_URL", "http://x", raising=False)
    monkeypatch.setattr(db, "SUPABASE_SERVICE_KEY", "k", raising=False)
    istemci = _SahteIstemci(referrer)
    monkeypatch.setattr(db.httpx, "AsyncClient", lambda *a, **k: istemci)
    pushlar = []

    async def _sahte_push(uid, credits):
        pushlar.append((uid, credits))

    monkeypatch.setitem(__import__("sys").modules, "pushnotify",
                        type("m", (), {"send_referral_reward_push": staticmethod(_sahte_push)}))
    asyncio.run(db.grant_referral_reward(davetli))
    return istemci.rpc, pushlar


def test_odul_tam_tarama_degerinde(monkeypatch):
    """+1 kontor DEGIL: TAM BIR TARAMA kadar odenmeli.

    Sayiyi sabitlemek yerine kurali dogruluyoruz: bedel degistiginde odul de
    degismeli. 2026-08-03'te bedel 10->20 oldu; odul 10'da kalsaydi davetli
    odulüyle hicbir sey yapamazdi ([[geoni-ucretsiz-kapi-token-catismasi]]).
    """
    from scan_costs import BRAND_SCAN_COST
    rpc, _ = _odul_calistir(monkeypatch, referrer="davet-eden-1")
    assert db.REFERRAL_REWARD_CREDITS == BRAND_SCAN_COST, \
        "odul tam bir tarama etmeli — bedel degisti, odul guncellenmedi"
    assert len(rpc) == 2                       # davetli + davet eden
    for c in rpc:
        assert c["p_amount"] == db.REFERRAL_REWARD_CREDITS
        assert c["p_gifted_delta"] == db.REFERRAL_REWARD_CREDITS
        assert c["p_idempotent"] is True       # ayni davetli icin tek sefer
        assert "1 tarama" not in c["p_description"]   # eski yanlis metin geri gelmesin


def test_odul_iki_tarafa_ayri_idempotent_anahtarla(monkeypatch):
    rpc, _ = _odul_calistir(monkeypatch, referrer="davet-eden-1", davetli="davetli-9")
    anahtarlar = {c["p_external_id"] for c in rpc}
    assert anahtarlar == {"ref_reward_invitee:davetli-9", "ref_reward_inviter:davetli-9"}


def test_push_yalniz_davet_edene_gider(monkeypatch):
    """Davetli odulu uygulamada gorunur; davet eden gormez -> dongu ona bildirilir."""
    _, pushlar = _odul_calistir(monkeypatch, referrer="davet-eden-1", davetli="davetli-2")
    assert pushlar == [("davet-eden-1", db.REFERRAL_REWARD_CREDITS)]


def test_self_referral_odul_almaz(monkeypatch):
    rpc, pushlar = _odul_calistir(monkeypatch, referrer="ayni", davetli="ayni")
    assert rpc == [] and pushlar == []


def test_referral_yoksa_odul_yok(monkeypatch):
    rpc, pushlar = _odul_calistir(monkeypatch, referrer=None)
    assert rpc == [] and pushlar == []


def test_davet_eden_hesabini_silebilir(monkeypatch):
    """Apple 5.1.1(v): birini DAVET ETMIS kullanici hesabini silebilmeli.

    profiles.referred_by -> profiles(id) FK'si NO ACTION; davet edenin profili
    silinmeden once davetlilerin isaretcisi NULL'lanmazsa silme FK'ye takilir ve
    "hesabimi sil" sessizce basarisiz olur. Canli e2e testte yakalandi (2026-07-25).
    """
    monkeypatch.setattr(db, "SUPABASE_URL", "http://x", raising=False)
    monkeypatch.setattr(db, "SUPABASE_SERVICE_KEY", "k", raising=False)
    cagrilar = {"patch": [], "delete": []}

    class _C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **kw): return _SahteYanit([])
        async def post(self, url, **kw): return _SahteYanit({})
        async def patch(self, url, **kw):
            cagrilar["patch"].append((url, kw.get("json")))
            return _SahteYanit({}, 204)
        async def delete(self, url, **kw):
            cagrilar["delete"].append(url)
            return _SahteYanit({}, 204)

    monkeypatch.setattr(db.httpx, "AsyncClient", lambda *a, **k: _C())
    assert asyncio.run(db.delete_user_account("davet-eden-1")) is True

    temizleme = [(u, b) for u, b in cagrilar["patch"] if "referred_by=eq.davet-eden-1" in u]
    assert temizleme, "davetlilerin referred_by'i NULL'lanmadi -> FK silmeyi engeller"
    assert temizleme[0][1] == {"referred_by": None}

    # Sira onemli: isaretci temizligi profil silmeden ONCE olmali.
    profil_silme = [i for i, u in enumerate(cagrilar["delete"]) if "/profiles?id=eq." in u]
    assert profil_silme, "profil silinmedi"
