"""A2-1 cache anahtari golden testi (Fable 2026-07-19 F-K1/K2/K3).

_cached_row_matches saf fonksiyonu: cache satiri istekle (dil+surum+nis) eslesir mi.
Bu test canli bug'lari kalicilastirir — regresyon olursa CI'da patlar."""
from db import _cached_row_matches


def _row(lang="tr", topic="entrepreneurship", sv="v5-gemini"):
    return {"lang": lang, "topic": topic, "scoring_version": sv, "score": 80}


def test_exact_match_true():
    assert _cached_row_matches(_row(), "tr", "entrepreneurship", "v5-gemini") is True


def test_lang_mismatch_false():
    # F-K1: EN satir TR istegine servis EDILMEZ
    assert _cached_row_matches(_row(lang="en"), "tr", "entrepreneurship", "v5-gemini") is False


def test_langless_legacy_row_false():
    # F-K1 kok neden: lang damgasi olmayan (None) legacy satir "tr" VARSAYILMAZ
    row = _row()
    del row["lang"]
    assert _cached_row_matches(row, "tr", "entrepreneurship", "v5-gemini") is False


def test_scoring_version_mismatch_false():
    # F-K2: v4-sov cache satiri v5 istegine servis EDILMEZ (v5 deploy cache'i gecersiz kilar)
    assert _cached_row_matches(_row(sv="v4-sov"), "tr", "entrepreneurship", "v5-gemini") is False


def test_scoring_version_none_ignored():
    # scoring_version None (cagiran gecmezse) -> surum kontrolu atlanir
    assert _cached_row_matches(_row(sv="v4-sov"), "tr", "entrepreneurship", None) is True


def test_topic_mismatch_false():
    # F-K3: ayni handle farkli nis -> eski nisin skoru servis EDILMEZ
    assert _cached_row_matches(_row(topic="entrepreneurship"), "tr", "wine and beverages", "v5-gemini") is False


def test_topic_normalized_true():
    # Buyuk/kucuk + bosluk normalize -> ayni nis tekrar taramada eslesir (pozitif yol korunur)
    assert _cached_row_matches(_row(topic="Entrepreneurship "), "tr", "  entrepreneurship", "v5-gemini") is True


def test_empty_topic_both_true():
    # Topic'siz marka taramasi: ikisi de bos -> eslesir
    assert _cached_row_matches(_row(topic=None), "tr", "", "v5-gemini") is True


def test_langless_and_topic_none_still_needs_lang():
    # None lang, istek "tr" -> yine eslesmez (lang zorunlu)
    assert _cached_row_matches({"topic": "", "scoring_version": "v5-gemini"}, "tr", "", "v5-gemini") is False
