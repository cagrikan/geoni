"""Google AI Overview takibi: ayristirma + sessiz-bozulma yasagi (2026-08-02).

Fixture'lar DataForSEO'nun RESMI dokumanindaki alan adlarina gore kuruldu
(docs.dataforseo.com/v3/serp/google/organic/live/advanced — ai_overview,
ai_overview_element, ai_overview_reference, link_element). Uydurulmus sema
uzerine test yazmak, canlida patlayan yesil test uretir.
"""
import asyncio

import ai_overview


def _aio_item(text="GEONI markası AI görünürlük ölçümü yapar.", refs=None, links=None):
    return {
        "type": "ai_overview",
        "asynchronous_ai_overview": True,
        "items": [{
            "type": "ai_overview_element",
            "title": "AI görünürlük",
            "text": text,
            "markdown": text,
            "links": links or [],
            "references": refs or [],
        }],
        "references": [],
    }


def _task(items):
    return {"status_code": 20000, "cost": 0.004, "result": [{"items": items}]}


# ---------- ayristirma ----------

def test_kutu_yoksa_hata_degil_olcumdur():
    out = ai_overview._parse_task(_task([{"type": "organic"}]), "GEONI", "geoni.ai")
    assert out["present"] is False
    assert out["brand_mentioned"] is False
    assert out["cited_domains"] == []


def test_marka_kutu_metninde_yakalanir():
    out = ai_overview._parse_task(_task([_aio_item()]), "GEONI", "geoni.ai")
    assert out["present"] is True
    assert out["brand_mentioned"] is True


def test_marka_gecmiyorsa_false():
    out = ai_overview._parse_task(
        _task([_aio_item(text="Profound ve Peec AI öne çıkıyor.")]), "GEONI", "geoni.ai")
    assert out["present"] is True
    assert out["brand_mentioned"] is False


def test_kaynaklar_element_ici_referans_ve_linkten_de_toplanir():
    """Yalniz ust seviye `references`e bakmak kaynaklarin bir kismini kaybettirir."""
    item = _aio_item(
        refs=[{"type": "ai_overview_reference", "domain": "searchenginejournal.com",
               "url": "https://searchenginejournal.com/x", "title": "X"}],
        links=[{"type": "link_element", "domain": "geoni.ai",
                "url": "https://geoni.ai/rehber", "title": "Rehber"}])
    item["references"] = [{"type": "ai_overview_reference", "domain": "example.com",
                           "url": "https://example.com/a", "title": "A"}]
    out = ai_overview._parse_task(_task([item]), "GEONI", "geoni.ai")
    assert set(out["cited_domains"]) == {"example.com", "searchenginejournal.com", "geoni.ai"}
    assert out["own_domain_cited"] is True


def test_ayni_domain_tekillestirilir():
    item = _aio_item(
        refs=[{"domain": "a.com", "url": "https://a.com/1"},
              {"domain": "a.com", "url": "https://a.com/2"}])
    out = ai_overview._parse_task(_task([item]), "GEONI", "geoni.ai")
    assert out["cited_domains"] == ["a.com"]


def test_domainsiz_referanstan_url_ile_domain_cikarilir():
    item = _aio_item(refs=[{"url": "https://www.hurriyet.com.tr/teknoloji/x"}])
    out = ai_overview._parse_task(_task([item]), "GEONI", "geoni.ai")
    assert out["cited_domains"] and "hurriyet" in out["cited_domains"][0]


# ---------- toplama / oranlar ----------

def _fake_rows(monkeypatch, rows):
    async def fake_one(client, sem, query, name, own_domain, lang):
        return {**rows.pop(0), "query": query}
    monkeypatch.setattr(ai_overview, "_one_query", fake_one)
    monkeypatch.setattr(ai_overview, "DFS_LOGIN", "x")
    monkeypatch.setattr(ai_overview, "DFS_PASSWORD", "y")


def test_oranlar_dogru_paydayi_kullanir(monkeypatch):
    """
    presence_rate paydasi TUM sorgular; mention_rate paydasi yalniz KUTU CIKAN
    sorgular. Kutu cikmayan sorguda gecmemek basarisizlik degil, olcum
    firsatinin hic dogmamasidir.
    """
    _fake_rows(monkeypatch, [
        {"present": True,  "brand_mentioned": True,  "cited_domains": ["a.com"], "cost": 0.004},
        {"present": True,  "brand_mentioned": False, "cited_domains": ["a.com", "b.com"], "cost": 0.004},
        {"present": False, "brand_mentioned": False, "cited_domains": [], "cost": 0.002},
        {"present": False, "brand_mentioned": False, "cited_domains": [], "cost": 0.002},
    ])
    out = asyncio.run(ai_overview.check_ai_overview(["q1", "q2", "q3", "q4"], "GEONI", "geoni.ai"))
    assert out["aio_present_count"] == 2
    assert out["aio_presence_rate"] == 0.5          # 2/4 — olculebilen sorgular
    assert out["brand_mention_count"] == 1
    assert out["brand_mention_rate"] == 0.5         # 1/2 — yalniz kutu cikanlar
    assert out["top_cited_domains"][0] == ("a.com", 2)
    assert out["cost_usd"] == 0.012


def test_hic_kutu_cikmazsa_sifira_bolme_yok(monkeypatch):
    _fake_rows(monkeypatch, [{"present": False, "brand_mentioned": False,
                              "cited_domains": [], "cost": 0.002}])
    out = asyncio.run(ai_overview.check_ai_overview(["q"], "GEONI"))
    assert out["brand_mention_rate"] == 0.0


# ---------- kapali/bozuk durumlar taramayi bozmaz ----------

def test_kimlik_yoksa_ozellik_sessizce_kapali(monkeypatch):
    monkeypatch.setattr(ai_overview, "DFS_LOGIN", "")
    monkeypatch.setattr(ai_overview, "DFS_PASSWORD", "")
    assert ai_overview.enabled() is False
    assert asyncio.run(ai_overview.check_ai_overview(["q"], "GEONI")) is None


def test_bos_sorgu_listesi_none(monkeypatch):
    monkeypatch.setattr(ai_overview, "DFS_LOGIN", "x")
    monkeypatch.setattr(ai_overview, "DFS_PASSWORD", "y")
    assert asyncio.run(ai_overview.check_ai_overview([], "GEONI")) is None
    assert asyncio.run(ai_overview.check_ai_overview(["  ", ""], "GEONI")) is None


def test_hata_yokluk_diye_sayilmaz(monkeypatch):
    """
    Olculdu 2026-08-02 (canli): 5 sorgunun biri 40101 dondu ve varlik orani
    5/5 yerine 4/5 gorundu. Hatali sorgu HER IKI paydadan da cikarilmali —
    yoksa "Google AI ozeti gostermiyor" diye YANLIS olcum raporlariz.
    """
    _fake_rows(monkeypatch, [
        {"present": True,  "brand_mentioned": True,  "cited_domains": [], "cost": 0.004},
        {"present": False, "brand_mentioned": False, "cited_domains": [], "cost": 0.0,
         "error": "task_40101"},
    ])
    out = asyncio.run(ai_overview.check_ai_overview(["q1", "q2"], "GEONI"))
    assert out["queries_measured"] == 1
    assert out["queries_failed"] == 1
    assert out["aio_presence_rate"] == 1.0     # 1/1, 1/2 DEGIL
    assert out["brand_mention_rate"] == 1.0


def test_hepsi_hata_ise_sifira_bolme_yok(monkeypatch):
    _fake_rows(monkeypatch, [{"present": False, "brand_mentioned": False,
                              "cited_domains": [], "cost": 0.0, "error": "exception"}])
    out = asyncio.run(ai_overview.check_ai_overview(["q"], "GEONI"))
    assert out["aio_presence_rate"] == 0.0 and out["queries_measured"] == 0


def test_yapilandirma_hatasinda_tekrar_denenmez(monkeypatch):
    """40402 gibi yapilandirma hatasinda ikinci deneme bosuna gecikme + maliyet."""
    calls = {"n": 0}

    class FakeResp:
        status_code = 200
        text = ""
        @staticmethod
        def json():
            return {"tasks": [{"status_code": 40402, "status_message": "Invalid Path."}]}

    class FakeClient:
        async def post(self, *a, **k):
            calls["n"] += 1
            return FakeResp()

    monkeypatch.setattr(ai_overview, "DFS_LOGIN", "x")
    monkeypatch.setattr(ai_overview, "DFS_PASSWORD", "y")
    asyncio.run(ai_overview._one_query(FakeClient(), asyncio.Semaphore(1),
                                       "q", "GEONI", "geoni.ai", "tr"))
    assert calls["n"] == 1


def test_gecici_saglayici_hatasinda_tekrar_denenir(monkeypatch):
    """40101 'Internal SE Server Error' gecicidir — bir kez tekrar denenir."""
    calls = {"n": 0}

    class FakeResp:
        status_code = 200
        text = ""
        @staticmethod
        def json():
            return {"tasks": [{"status_code": 40101, "status_message": "Internal SE Server Error."}]}

    class FakeClient:
        async def post(self, *a, **k):
            calls["n"] += 1
            return FakeResp()

    monkeypatch.setattr(ai_overview, "DFS_LOGIN", "x")
    monkeypatch.setattr(ai_overview, "DFS_PASSWORD", "y")
    async def _no_wait(*_a, **_k):   # asyncio.sleep'i CAGIRMA — kendini patchleyip rekursiyona girer
        return None
    monkeypatch.setattr(ai_overview.asyncio, "sleep", _no_wait)
    out = asyncio.run(ai_overview._one_query(FakeClient(), asyncio.Semaphore(1),
                                             "q", "GEONI", "geoni.ai", "tr"))
    assert calls["n"] == 2
    assert out["error"] == "task_40101"


def test_gorev_seviyesi_hatasi_ust_seviye_200_olsa_da_yakalanir(monkeypatch):
    """
    Olculdu 2026-08-02: ust seviye status 20000 "Ok." donerken gorev seviyesi
    40402 "Invalid Path" doneblliyor. Yalniz HTTP koduna bakmak yanlis veri uretir.
    """
    class FakeResp:
        status_code = 200
        text = ""
        @staticmethod
        def json():
            return {"status_code": 20000,
                    "tasks": [{"status_code": 40402, "status_message": "Invalid Path."}]}

    class FakeClient:
        async def post(self, *a, **k):
            return FakeResp()

    monkeypatch.setattr(ai_overview, "DFS_LOGIN", "x")
    monkeypatch.setattr(ai_overview, "DFS_PASSWORD", "y")
    out = asyncio.run(ai_overview._one_query(
        FakeClient(), asyncio.Semaphore(1), "q", "GEONI", "geoni.ai", "tr"))
    assert out["error"] == "task_40402"
    assert out["present"] is False
