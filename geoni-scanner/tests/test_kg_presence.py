"""
T8 offline birim testleri: Google Knowledge Graph yanit parse'i.

Ag cagrisi YOK — scoring._parse_kg_response saf fonksiyonu sahte KG JSON'uyla
test edilir (ad eslesmesi + resultScore esigi mantigi).
"""
import scoring


def _kg(name, score):
    return {"itemListElement": [
        {"@type": "EntitySearchResult",
         "result": {"name": name}, "resultScore": score},
    ]}


def test_kg_strong_match_present():
    res = scoring._parse_kg_response(_kg("GEONI", 250.0), "GEONI")
    assert res["present"] is True
    assert res["score"] == 250.0


def test_kg_name_normalization_matches_suffix():
    # 'Geoni.ai' etiketi 'GEONI' ile normalize eslesir (uzanti toleransi)
    res = scoring._parse_kg_response(_kg("Geoni.ai", 120.0), "GEONI")
    assert res["present"] is True


def test_kg_low_score_matches_name_but_not_present():
    # Ad eslesir ama resultScore esigin (20) altinda -> adas/gurultu, present False
    res = scoring._parse_kg_response(_kg("Acme", 5.0), "Acme")
    assert res["present"] is False
    assert res["score"] == 5.0


def test_kg_no_name_match():
    res = scoring._parse_kg_response(_kg("Baska Sirket", 900.0), "Acme")
    assert res["present"] is False
    assert res["score"] == 0.0


def test_kg_empty_response():
    res = scoring._parse_kg_response({}, "Acme")
    assert res == {"present": False, "score": 0.0}


def test_kg_picks_matching_over_first():
    data = {"itemListElement": [
        {"result": {"name": "Alakasiz"}, "resultScore": 999.0},
        {"result": {"name": "Acme"}, "resultScore": 77.0},
    ]}
    res = scoring._parse_kg_response(data, "Acme")
    assert res["present"] is True
    assert res["score"] == 77.0
