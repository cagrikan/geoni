"""Kisi/marka/sosyal rapor e-postasi (2026-07-30).

Bulgu: mobil (`res_scan_timeout`, `res_email_note`) ve web (`error_query_timeout`)
"sonucu e-postana gondereceğiz" diyordu, ama send_audit_report_email YALNIZCA web
(site) taramasinda cagriliyordu -- 4 tarama tipinin 3'unde hicbir posta cikmiyordu.
Bu testler sozun tutuldugunu ve sablonun brand payload'ini DOGRU okudugunu korur.
"""
import asyncio

import mailer


BRAND_PAYLOAD = {
    "name": "Örnek Kişi",
    "score": 72,
    "score_breakdown": {
        "claude": 83.5, "chatgpt": 81.7, "gemini": 93.8, "perplexity": 92.1,
        "yanit_kalitesi": 85.8, "konu_uyumu": 64.0, "kategori_gorunurlugu": 14.0,
    },
    # DIKKAT: brand payload'i web'den FARKLI adlar kullanir.
    "performing_topics": [{"topic": "Dijital Pazarlama", "mentions": 4}],
    "opportunity_topics": [{"topic": "Yapay Zekâ Otomasyonu", "mentions": 0}],
    "sov": {"checked": True, "score": 40, "mention_count": 2, "query_count": 5,
            "queries": [{"query": "en iyi pazarlamacı", "mentioned": True}],
            "sources": [{"domain": "ornek.com"}]},
    "created_at": "2026-07-30T12:00:00",
}


def test_brand_html_konu_listelerini_okur():
    """performing_topics/opportunity_topics okunmazsa e-posta 'konu yok' derdi."""
    html = mailer._build_report_html("Örnek Kişi", BRAND_PAYLOAD, "tr", brand=True)
    assert "Dijital Pazarlama" in html
    assert "Yapay Zekâ Otomasyonu" in html


def test_brand_html_kirilim_etiketleri_cevrilmis():
    """Etiket eslesmezse ham anahtar ('yanit_kalitesi') musteriye gorunurdu."""
    html = mailer._build_report_html("Örnek Kişi", BRAND_PAYLOAD, "tr", brand=True)
    assert "Yanıt Kalitesi" in html and "Kategori Görünürlüğü" in html
    assert "yanit_kalitesi" not in html and "kategori_gorunurlugu" not in html

    html_en = mailer._build_report_html("Örnek Kişi", BRAND_PAYLOAD, "en", brand=True)
    assert "Answer Quality" in html_en and "Category Visibility" in html_en


def test_brand_html_alan_adi_degil_ad_der():
    """Kisi taramasinda 'Taranan Alan Adi' basligi yanlis olurdu."""
    html = mailer._build_report_html("Örnek Kişi", BRAND_PAYLOAD, "tr", brand=True)
    assert "Taranan Alan Adı" not in html
    assert "Örnek Kişi" in html


def test_web_sablonu_bozulmadi():
    """Ayni fonksiyon web raporunu da uretiyor; brand eklemesi onu kirmamali."""
    web = {"score": 55, "breakdown": {"authority": 40, "ai_access": 70},
           "top_topics": [{"topic": "SEO"}], "opportunities": [],
           "created_at": "2026-07-30T12:00:00"}
    html = mailer._build_report_html("ornek.com", web, "tr")
    # Etiket 2026-08-02'de "Otorite" -> "Güvenilirlik Sinyali" oldu (jargon
    # temizligi). Kirilim etiketinin e-postaya BASILDIGINI dogrulamak testin
    # amaci; hangi kelime oldugu web/mobil ile ayni kalmali (bkz. mailer.py).
    assert "Taranan Alan Adı" in html and "Güvenilirlik Sinyali" in html and "SEO" in html


def test_bos_adres_ag_istegi_yapmadan_false_doner():
    """Anonim sosyal taramada e-posta bos olabilir; bos adrese POST atilmamali."""
    def patlat(*a, **k):  # pragma: no cover - cagrilirsa test zaten duser
        raise AssertionError("bos adres icin HTTP istegi yapildi")

    eski = mailer.httpx.AsyncClient
    mailer.httpx.AsyncClient = patlat
    try:
        assert asyncio.run(mailer.send_brand_report_email("", "Örnek", BRAND_PAYLOAD)) is False
    finally:
        mailer.httpx.AsyncClient = eski


def test_anahtar_yoksa_sessizce_false(monkeypatch):
    """Fail-silent: e-posta yapilandirilmamissa tarama BASARILI sayilmali."""
    monkeypatch.setattr(mailer, "RESEND_API_KEY", "")
    assert asyncio.run(mailer.send_brand_report_email("a@b.com", "Örnek", BRAND_PAYLOAD)) is False


def test_konu_basligi_kacislanir():
    """Konu metni LLM'den geliyor; HTML'e ham gomulmemeli (XSS/bozuk sablon)."""
    kotu = {**BRAND_PAYLOAD, "performing_topics": [{"topic": "<script>x</script>"}]}
    html = mailer._build_report_html("Örnek", kotu, "tr", brand=True)
    assert "<script>" not in html


# ── Adres cozumu ────────────────────────────────────────────────────────
# Mobil kisi/marka taramasi govdede HIC e-posta gondermiyor; web ise adres
# yoksa 'anonymous@geoni.ai' yer tutucusunu koyuyor. Ikisi de duzeltilmezse
# rapor postasi ya hic gitmez ya da olmayan bir kutuya gider.
# NOT: `main` import EDILMEZ (deploy kapisi minimal ortamda kosuyor, fastapi yok).


def test_gercek_adres_oldugu_gibi_kullanilir():
    assert asyncio.run(mailer.rapor_adresi(" a@b.com ", "u1")) == "a@b.com"


def test_yer_tutucu_hesap_adresine_duser(monkeypatch):
    async def sahte(uid):
        assert uid == "u1"
        return "hesap@ornek.com"
    monkeypatch.setattr("db.get_auth_email", sahte)
    assert asyncio.run(mailer.rapor_adresi("anonymous@geoni.ai", "u1")) == "hesap@ornek.com"
    assert asyncio.run(mailer.rapor_adresi("ANONYMOUS@GEONI.AI", "u1")) == "hesap@ornek.com"


def test_bos_adres_hesap_adresine_duser(monkeypatch):
    async def sahte(uid):
        return "hesap@ornek.com"
    monkeypatch.setattr("db.get_auth_email", sahte)
    assert asyncio.run(mailer.rapor_adresi(None, "u1")) == "hesap@ornek.com"


def test_anonim_kullanicida_bos_doner():
    """Giris yoksa (anonim sosyal tarama) gidecek adres YOK -- posta atlanir."""
    assert asyncio.run(mailer.rapor_adresi("", None)) == ""
    assert asyncio.run(mailer.rapor_adresi("anonymous@geoni.ai", None)) == ""
