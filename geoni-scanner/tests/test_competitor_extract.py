"""A4-2: rakip cikarimi filtreleme + sessiz-[] yasagi (QA 2026-07-19)."""
import asyncio
import sov


def test_filter_drops_own_and_platforms():
    comps = [{"name": "Trendyol"}, {"name": "Hepsiburada"}, {"name": "ChatGPT"},
             {"name": "Amazon"}, {"name": "Google Gemini"}]
    answers = ["Trendyol Hepsiburada Amazon populer secenekler."]
    out = sov._filter_competitors(comps, "Trendyol", answers)
    names = [c["name"] for c in out]
    assert "Hepsiburada" in names and "Amazon" in names
    assert "Trendyol" not in names          # kendi markasi
    assert "ChatGPT" not in names           # AI platformu
    assert "Google Gemini" not in names     # AI platformu (varyant)


def test_filter_dedups_and_caps_five():
    comps = [{"name": f"Brand{i}"} for i in range(8)] + [{"name": "Brand0"}]
    out = sov._filter_competitors(comps, "Own", ["Brand0 Brand1 Brand2"])
    assert len(out) == 5
    assert len([c for c in out if c["name"] == "Brand0"]) == 1


def test_empty_llm_returns_empty_not_crash():
    async def dead_llm(_):
        return None  # saglayici dusuk / bos
    out = asyncio.run(sov._extract_competitors(["gercek yanit metni"], "X", dead_llm))
    assert out == []


def test_retry_then_success():
    calls = {"n": 0}
    async def flaky_llm(_):
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # ilk deneme bos
        return '{"competitors": [{"name": "Rakip A"}]}'
    out = asyncio.run(sov._extract_competitors(["Rakip A metinde geciyor"], "X", flaky_llm))
    assert calls["n"] == 2                    # retry oldu
    assert [c["name"] for c in out] == ["Rakip A"]


def test_kirpilmis_json_kurtarilir():
    """Kok neden regresyon testi (2026-08-02): max_tokens tavaninda kesilen yanit.

    Uretimde 7 gunde 26/26 'JSON parse basarisiz' bu sekilde olusuyordu; hem
    json.loads hem `[\\[{].*[\\]}]` regex'i kapanis parantezi olmadigi icin
    dusuyor, rakip listesi sessizce BOS kaliyordu."""
    kirpik = ('{"competitors": [{"name": "Rakip A", "mentions": 2}, '
              '{"name": "Rakip B", "mentions": 1}, {"name": "Rakip C", "menti')

    async def kirpan_llm(_p, **_kw):
        return kirpik

    # Eski yol gercekten dusuyor mu — testin bir seyi kanitladigindan emin ol.
    assert sov._extract_json(kirpik) is None

    out = asyncio.run(sov._extract_competitors(
        ["Rakip A ve Rakip B onerilir.", "Rakip A one cikiyor."], "X", kirpan_llm))
    names = [c["name"] for c in out]
    assert names[:2] == ["Rakip A", "Rakip B"]   # tam nesneler kurtarildi
    assert "Rakip C" not in names                # yarim nesne UYDURULMAZ


def test_max_tokens_desteklemeyen_llm_bozulmaz():
    """ask_llm sozlesmesi tek argumanli olabilir (testlerdeki sahte LLM'ler,
    eski cagiranlar). TypeError yakalanip tek argumanla tekrar denenmeli."""
    calls = {"n": 0}

    async def tek_argumanli(_p):          # kwarg KABUL ETMIYOR
        calls["n"] += 1
        return '{"competitors": [{"name": "Rakip A"}]}'

    out = asyncio.run(sov._extract_competitors(["Rakip A geciyor"], "X", tek_argumanli))
    assert [c["name"] for c in out] == ["Rakip A"]
    assert calls["n"] == 1


def test_salvage_ic_ice_olmayan_semada_yarim_nesneyi_atar():
    assert sov._salvage_objects('[{"name":"A"},{"name":"B"},{"na') == \
        [{"name": "A"}, {"name": "B"}]
    assert sov._salvage_objects('{"name":""}') == []      # bos ad alinmaz
    assert sov._salvage_objects("") == []
