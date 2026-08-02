"""Uzman brief'ine SAYFA TIPI ipucu (2026-08-02).

NEDEN: uzmana giden brief bugune kadar "NE yazilacagini" soyluyordu (bosluk,
firsat, rakip) ama "HANGI BICIMDE" yazilacagini soylemiyordu. Olculdu: AI'in
alintiladigi sayfalarin ~%53'u karsilastirma listesi, rehber yalnizca ~%3 —
ve cogu musteri (biz dahil) rehber uretiyor. Ayni konu, yanlis bicimde
yazildiginda alintilanmiyor.

Ipucu DETERMINISTIK: tarama verisinden gelir, LLM cagrisi yoktur.
"""
import ticket_automation as ta


def _audit(gap=None, **ek):
    r = {"sov": {"checked": True, "queries": [], "competitors": []}}
    r.update(ek)
    if gap is not None:
        r["page_type_gap"] = {"eksik_tipler": gap}
    return {"result_json": r}


def test_sayfa_tipi_secime_giriyor():
    a = _audit(gap=[{"tip": "liste/karsilastirma", "ai_orani": 0.525,
                     "bizdeki_sayfa": 1, "kat": 4.7}])
    sel = ta._select_content_topics(a)
    assert sel["page_type_gap"][0]["tip"] == "liste/karsilastirma"
    assert sel["page_type_gap"][0]["kat"] == 4.7


def test_TEK_BASINA_hammadde_sayilir():
    """
    Baska sinyal olmasa bile sayfa tipi eksigi brief uretmeye yeter: "hangi
    bicimde yazilacagi" tek basina degerli bir yonlendirmedir.
    """
    a = _audit(gap=[{"tip": "liste/karsilastirma", "ai_orani": 0.5,
                     "bizdeki_sayfa": 0, "kat": None}])
    sel = ta._select_content_topics(a)
    assert sel["gaps"] == [] and sel["opportunities"] == [] and sel["competitors"] == []
    assert ta._content_has_material(sel) is True


def test_gap_yoksa_eski_davranis_bozulmaz():
    a = _audit(gap=None)
    sel = ta._select_content_topics(a)
    assert sel["page_type_gap"] == []
    assert ta._content_has_material(sel) is False


def test_bozuk_kayit_patlamaz():
    """Tip adi bos/yanlis tipteyse atlanir, brief uretimi cokmez."""
    a = _audit(gap=[{"tip": "", "ai_orani": 0.5}, {"ai_orani": 0.4},
                    {"tip": "hizmet/urun", "ai_orani": 0.3, "bizdeki_sayfa": 1, "kat": 2.5}])
    sel = ta._select_content_topics(a)
    assert [t["tip"] for t in sel["page_type_gap"]] == ["hizmet/urun"]


def test_en_fazla_uc_tip():
    gap = [{"tip": f"tip{i}", "ai_orani": 0.5, "bizdeki_sayfa": 0} for i in range(6)]
    sel = ta._select_content_topics(_audit(gap=gap))
    assert len(sel["page_type_gap"]) == 3


def test_sanitize_uygulaniyor():
    """Tarama verisi dis kaynakli; prompt'a ham girmemeli (enjeksiyon yuzeyi)."""
    a = _audit(gap=[{"tip": "x" * 200, "ai_orani": 0.5, "bizdeki_sayfa": 0}])
    sel = ta._select_content_topics(a)
    assert len(sel["page_type_gap"][0]["tip"]) <= 40
