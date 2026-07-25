"""A1-2: judge medyan-of-3 birlestirme (QA 2026-07-19). Tek judge ornegi claude
alt-skorunda ~D55 oynaklik uretiyordu; medyan varyansi dusurur."""
import asyncio
import brand_recall as b


def _run_stable(samples, model_texts):
    it = iter(samples)
    # cache kwarg (2026-07-25 prompt caching): _stable artik ilk ornegi tek basina,
    # kalanini paralel cagirir ve hepsine cache=True gecer — sahte judge de kabul etmeli.
    async def fake(mt, wr, pi, cache=False):
        return next(it)
    orig, orig_n = b.judge_batch_accuracy, b.JUDGE_SAMPLES
    b.judge_batch_accuracy = fake
    b.JUDGE_SAMPLES = len(samples)
    try:
        return asyncio.run(b.judge_batch_accuracy_stable(model_texts, [], {}))
    finally:
        b.judge_batch_accuracy, b.JUDGE_SAMPLES = orig, orig_n


def _entry(score, olgu=0, celiski=False, uydurma=False, duygu="notr", izi="belirsiz"):
    return {"dogruluk_skoru": score, "dogrulanmis_olgu_sayisi": olgu,
            "celiski_var": celiski, "uydurma_suphesi": uydurma,
            "duygu": duygu, "bilgi_izi": izi}


def test_median_score_smooths_outlier():
    samples = [{"claude": _entry(30)}, {"claude": _entry(85)}, {"claude": _entry(70)}]
    r = _run_stable(samples, {"claude": "x"})
    assert r["claude"]["dogruluk_skoru"] == 70.0   # medyan, ne 30 ne 85


def test_booleans_majority_vote():
    samples = [{"claude": _entry(70, celiski=True)},
               {"claude": _entry(70, celiski=True)},
               {"claude": _entry(70, celiski=False)}]
    r = _run_stable(samples, {"claude": "x"})
    assert r["claude"]["celiski_var"] is True      # 2/3 cogunluk


def test_categorical_mode():
    samples = [{"claude": _entry(70, duygu="pozitif")},
               {"claude": _entry(70, duygu="pozitif")},
               {"claude": _entry(70, duygu="notr")}]
    r = _run_stable(samples, {"claude": "x"})
    assert r["claude"]["duygu"] == "pozitif"


def test_single_sample_passthrough():
    # JUDGE_SAMPLES=1 -> eski davranis (tek cagri).
    samples = [{"claude": _entry(42)}]
    r = _run_stable(samples, {"claude": "x"})
    assert r["claude"]["dogruluk_skoru"] == 42.0


def test_stable_uses_prompt_cache():
    """MALIYET korumasi (2026-07-25): _stable TUM judge orneklerini cache=True ile
    cagirmali. Bir refactor bunu dusurursa judge girdi tokeni tekrar tam fiyattan
    faturalanir (~%36 maliyet artisi) ve kimse fark etmez — bu test onu yakalar."""
    seen = []

    async def fake(mt, wr, pi, cache=False):
        seen.append(cache)
        return {"claude": _entry(70)}

    orig, orig_n = b.judge_batch_accuracy, b.JUDGE_SAMPLES
    b.judge_batch_accuracy, b.JUDGE_SAMPLES = fake, 3
    try:
        asyncio.run(b.judge_batch_accuracy_stable({"claude": "x"}, [], {}))
    finally:
        b.judge_batch_accuracy, b.JUDGE_SAMPLES = orig, orig_n
    assert seen == [True, True, True], f"tum ornekler cache'li olmali, gelen: {seen}"
