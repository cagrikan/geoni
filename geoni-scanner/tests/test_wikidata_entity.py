"""wikidata_entity yarı-otonom taslak (2.5) — deterministik, LLM/ağ YOK, offline."""
import ticket_automation as T


def _audit(inferred_name="Örnek Klinik", inferred_topic="diş kliniği", atype="web",
           same_as=None, sources=None, citation_gap=None):
    return {"type": atype, "lang": "tr", "result_json": {
        "brand_recall": {"inferred_name": inferred_name, "inferred_topic": inferred_topic},
        "site_assets": {"sameAs": same_as or []},
        "sov": {"checked": True, "sources": sources or [], "citation_gap": citation_gap or []},
    }}


# ── SEMI_AUTO wiring ────────────────────────────────────────────────────────
def test_semi_auto_wiring():
    assert "wikidata_entity" in T.SEMI_AUTO_KEYS
    # AUTO değil — yayınlama insan işi (submitted YAPILMAZ)
    assert "wikidata_entity" not in T.AUTO_FULFILL_KEYS


# ── deterministik label / P31 ───────────────────────────────────────────────
def test_name_from_inferred_name():
    assert T._wikidata_name("ornekklinik.com", _audit()) == "Örnek Klinik"


def test_name_falls_back_to_domain_when_sentence_like():
    a = _audit(inferred_name="bu bir cümle gibi görünen çok uzun altı kelimeyi aşan tahmin")
    assert T._wikidata_name("ornekklinik.com", a) == "ornekklinik.com"


def test_name_none_without_audit():
    # audit yoksa → insana düş (çöp kayıt üretme)
    assert T._wikidata_name("ornekklinik.com", None) is None


def test_p31_mapping():
    assert T._wikidata_p31("web") == "Q4830453"
    assert T._wikidata_p31("brand") == "Q4830453"
    assert T._wikidata_p31("person") == "Q5"
    assert T._wikidata_p31("social") == "Q5"


# ── sosyal property çıkarımı ────────────────────────────────────────────────
def test_social_claims_extraction():
    claims = dict(T._wikidata_social_claims([
        "https://instagram.com/ornekklinik",
        "https://x.com/ornek_k",
        "https://www.facebook.com/ornekklinik",
        "https://www.youtube.com/@ornekklinik",
        "https://www.linkedin.com/company/ornek-klinik",
        "https://tiktok.com/@ornekklinik",
    ]))
    assert claims["P2003"] == "ornekklinik"   # Instagram
    assert claims["P2002"] == "ornek_k"       # X
    assert claims["P2013"] == "ornekklinik"   # Facebook
    assert claims["P2397"] == "ornekklinik"   # YouTube
    assert claims["P4264"] == "ornek-klinik"  # LinkedIn şirket
    assert claims["P7085"] == "ornekklinik"   # TikTok


def test_social_claims_dedup_and_noise():
    # aynı property tek kez; gürültü segmenti (sharer/home) elenir
    claims = T._wikidata_social_claims([
        "https://facebook.com/sharer",
        "https://facebook.com/gercekhesap",
        123,  # str olmayan → atlanır
    ])
    assert claims == [("P2013", "gercekhesap")]


# ── notability kaynak adayları ──────────────────────────────────────────────
def test_notability_sources_excludes_own_and_dedups():
    a = _audit(sources=[{"domain": "kendi.com", "mentions": 5, "own": True},
                        {"domain": "haber.com", "mentions": 3, "own": False},
                        {"domain": "haber.com", "mentions": 1, "own": False}],
               citation_gap=[{"domain": "dizin.com"}, {"domain": "haber.com"}])
    src = T._wikidata_notability_sources(a)
    assert "kendi.com" not in src
    assert src == ["haber.com", "dizin.com"]


# ── QuickStatements format (CREATE) ─────────────────────────────────────────
def test_quickstatements_create_format():
    qs = T.build_quickstatements(
        name="Örnek Klinik", p31="Q4830453", domain="ornekklinik.com",
        desc_tr="Türk diş kliniği", desc_en="Turkish dental clinic",
        social_claims=[("P2003", "ornekklinik")],
        source_url="https://haber.com/x", today="2026-07-19", qid=None)
    lines = qs.strip().splitlines()
    assert lines[0] == "CREATE"
    assert 'LAST|Ltr|"Örnek Klinik"' in qs
    assert 'LAST|Len|"Örnek Klinik"' in qs
    assert 'LAST|Dtr|"Türk diş kliniği"' in qs
    assert 'LAST|Den|"Turkish dental clinic"' in qs
    assert "LAST|P31|Q4830453" in qs
    # P856 referanslı (S854 kaynak + S813 tarih)
    assert 'LAST|P856|"https://ornekklinik.com"|S854|"https://haber.com/x"|S813|+2026-07-19T00:00:00Z/11' in qs
    assert 'LAST|P2003|"ornekklinik"' in qs


def test_quickstatements_enrich_no_create_no_label():
    # QID varsa (zenginleştirme): CREATE/label/description YAZILMAZ, item = QID
    qs = T.build_quickstatements(
        name="Örnek Klinik", p31="Q4830453", domain="ornekklinik.com",
        desc_tr="x", desc_en="y", social_claims=[("P2003", "ornekklinik")],
        source_url="", today="2026-07-19", qid="Q12345")
    assert "CREATE" not in qs
    assert "Ltr" not in qs and "Dtr" not in qs
    assert "Q12345|P31|Q4830453" in qs
    assert 'Q12345|P856|"https://ornekklinik.com"' in qs  # referanssız (source_url boş)
    assert 'Q12345|P2003|"ornekklinik"' in qs


def test_qs_escapes_quotes_and_pipes():
    qs = T.build_quickstatements(
        name='Zar"lı|isim', p31="", domain=None, desc_tr=None, desc_en=None,
        social_claims=[], source_url="", today="2026-07-19", qid=None)
    assert '"' not in qs.split("Ltr|")[1].split("\n")[0].strip('"')  # iç tırnak temizlendi
    assert "|isim" not in qs  # pipe temizlendi (format kırılmaz)


# ── schema.org QID köprüsü (M2) ─────────────────────────────────────────────
def test_schema_html_adds_wikidata_sameas():
    a = _audit(same_as=["https://instagram.com/ornekklinik"])
    html = T.generate_schema_html("ornekklinik.com", a, wikidata_qid="Q12345")
    assert "https://www.wikidata.org/wiki/Q12345" in html
    assert '"@id"' in html  # @id/sameAs üçgeni
    # mevcut sameAs korunur
    assert "https://instagram.com/ornekklinik" in html


def test_schema_html_no_qid_unchanged():
    a = _audit(same_as=["https://instagram.com/ornekklinik"])
    html = T.generate_schema_html("ornekklinik.com", a)  # QID yok
    assert "wikidata.org" not in html


def test_schema_html_qid_from_audit_field():
    a = _audit()
    a["result_json"]["wikidata_qid"] = "Q99"
    html = T.generate_schema_html("ornekklinik.com", a)
    assert "https://www.wikidata.org/wiki/Q99" in html


def test_schema_html_invalid_qid_ignored():
    a = _audit()
    html = T.generate_schema_html("ornekklinik.com", a, wikidata_qid="not-a-qid")
    assert "wikidata.org" not in html


# ── rapor içeriği (dürüst taslak etiketi, C-1) ──────────────────────────────
def test_report_create_mentions_draft_and_human_publish():
    r = T._build_wikidata_report(
        name="Örnek Klinik", qid=None, p31="Q4830453", domain="ornekklinik.com",
        desc_tr="Türk diş kliniği", desc_en="Turkish dental clinic",
        social_claims=[("P2003", "ornekklinik")], sources=["haber.com"],
        atype="web", today="2026-07-19")
    assert "taslak" in r.lower()
    assert "CREATE" in r
    assert "notability" in r.lower() or "kayda-değerlik" in r.lower()
    assert "haber.com" in r  # notability kaynak adayı


def test_report_enrich_mentions_existing_record():
    r = T._build_wikidata_report(
        name="Örnek", qid="Q12345", p31="Q4830453", domain="ornek.com",
        desc_tr=None, desc_en=None, social_claims=[], sources=[],
        atype="web", today="2026-07-19")
    assert "Q12345" in r
    assert "zenginle" in r.lower()  # zenginleştirme
