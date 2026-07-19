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
