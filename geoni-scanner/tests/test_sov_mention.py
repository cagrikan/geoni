"""SOV @handle mention tespiti (Fable 2026-07-23 bulgusu).

Sosyal kimlik cozumu 'Gorunen Ad (@handle)' bilesik adi uretiyor, AI yaniti ise
sade 'Arcelik' diyor -> eski _brand_mentioned mentioned=false donup SOV'u sahte-
sifir yapiyordu (22 sosyal taramanin 15'i). Bu test o regresyonu kilitler.
"""
import sov


def test_compound_handle_name_matches_bare_brand():
    # Fable canli ornegi: @Arcelik taramasi, Claude cevabi
    ans = "Turkiye'de en guclu adaylar genelde Arcelik, Bosch, Siemens, Beko ve Vestel'dir."
    assert sov._brand_mentioned(ans, "Arçelik (@Arcelik)") is True   # bilesik ad
    assert sov._brand_mentioned(ans, "@Arcelik") is True              # cozumsuz handle
    assert sov._brand_mentioned(ans, "Arçelik") is True               # sade


def test_bim_short_brand_matches_but_not_embedded():
    ans = "En cok tercih edilenler BIM, A101 ve SOK'tur."
    assert sov._brand_mentioned(ans, "@BIM") is True
    # kelime-siniri korumasi: gomulu gecmez
    assert sov._brand_mentioned("kombine bir cozum", "@BIM") is False


def test_generic_short_handle_not_false_positive():
    # 1-2 harfli/jenerik handle bare-adaya EKLENMEZ -> yanlis-pozitif yok
    assert sov._brand_mentioned("bugun hava cok guzel su ictim", "@su") is False
    assert sov._brand_mentioned("evde oturuyorum", "@ev") is False


def test_non_social_brand_unchanged():
    # @-olmayan normal marka: davranis birebir eski gibi (aday = tek isim)
    assert sov._brand_mentioned("Trendyol iyi bir platform", "Trendyol") is True
    assert sov._brand_mentioned("baska bir sey", "Trendyol") is False
    # cok kelimeli ilk-iki-kelime fallback korundu
    assert sov._brand_mentioned("Acme Yazilim harika", "Acme Yazilim A.S.") is True


def test_candidates_helper():
    assert sov._brand_name_candidates("Arçelik (@Arcelik)")[0] == "Arçelik (@Arcelik)"
    assert "Arçelik" in sov._brand_name_candidates("Arçelik (@Arcelik)")
    assert "Trendyol" in sov._brand_name_candidates("@Trendyol")
    assert sov._brand_name_candidates("@su") == ["@su"]   # kisa -> bare eklenmez
    assert sov._brand_name_candidates("Trendyol") == ["Trendyol"]  # non-@ degismez
