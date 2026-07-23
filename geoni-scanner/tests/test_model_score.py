"""B7 canliya alindi (Fable backtest 2026-07-23): _model_score_from_components
artik sert 30-tavani yerine KADEMELI ceza uyguluyor (celiski ×0.55, uydurma ×0.70).
Gerekce: gercek veride "yalniz celiski_var" grubu medyan dogruluk 72 iken 30'a
eziliyordu (judge yanlis-pozitifi). Bu testler yeni davranisi kilitler + canli
fonksiyonun golge fonksiyonla ozdes oldugunu dogrular."""
import brand_recall as b


def _base():
    # guven=80, dogruluk=72, uzunluk=50 -> ham = 72*.70 + 80*.25 + 50*.05 = 72.9
    return dict(taniyor=True, guven=80, dogruluk_skoru=72, uzunluk_skoru=50)


def _score(**over):
    a = _base(); a.update(over)
    return b._model_score_from_components(
        a["taniyor"], a["guven"], a["dogruluk_skoru"], a["uzunluk_skoru"],
        a.get("uydurma_suphesi", False), a.get("celiski_var", False),
    )


def test_no_flags_full_raw():
    assert _score() == 72.9


def test_celiski_only_gradual_not_capped():
    # Eski davranis 30 tavani -> 30.0; YENI: 72.9 * 0.55 = 40.1 (haksiz ezilme yok).
    s = _score(celiski_var=True)
    assert s == 40.1
    assert s > 30.0   # asil duzeltme: yuksek-dogruluk celiski grubu artik 30'a inmiyor


def test_uydurma_only_gradual():
    assert _score(uydurma_suphesi=True) == 51.0   # 72.9 * 0.70


def test_both_flags_compound():
    # Ikisi birden ×0.385 -> gercekten kotu/celiskili yanit sert cezalanir (tavandan DA sert).
    assert _score(celiski_var=True, uydurma_suphesi=True) == 28.1
    assert _score(celiski_var=True, uydurma_suphesi=True) < 30.0


def test_not_recognized_zero():
    assert _score(taniyor=False) == 0.0


def test_live_equals_shadow_penalty():
    """B7 canliya alindi -> per-model canli skor golge fonksiyonla ozdes olmali
    (B6 = audit-duzeyi agirlik dagitimi golgede kaldi, per-model'i etkilemez)."""
    for flags in [(False, False), (True, False), (False, True), (True, True)]:
        cel, uyd = flags
        live = b._model_score_from_components(True, 80, 72, 50, uyd, cel)
        shadow = b._model_score_shadow(True, 80, 72, 50, uyd, cel)
        assert live == shadow, f"flags={flags}: live={live} shadow={shadow}"
