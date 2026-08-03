"""Web tarama payload'i: TEK insa noktasi sozlesmesi (2026-08-02).

🪤 YASANDI: bu payload main.py ve monitor.py'de ELLE KOPYALANMISTI. main.py'de
`platforms.google` duzeltildi (Google SONUC SAYISI degil, bot-izni BOOLEAN'i),
monitor.py'deki kopya eski satirda kaldi -> izleme taramalari "Gemini sitenize
erisemiyor" yanlis bulgusunu ve yanindaki "Hizmete git" butonunu GERI
GETIRECEKTI. Ayni sapmada `ssr`/`citability`/`page_type_gap` monitor
payload'inda hic yoktu: SSR cezasi skoru dusururken kullanici sebebini
goremiyordu.

Bu dosya iki seyi birden korur:
  1) Payload'in DAVRANISI (google alani boolean okur, yeni alanlar tasiniyor).
  2) YAPI: iki cagiran da payload'i elle kurmaz, ortak fonksiyondan gecer —
     yani ayni sapma bir daha yazilamaz.
"""
import asyncio
import pathlib

import audit_payload


def _payload(indexing_status=None, crawl=None, **ek):
    async def _sahte_stability(*a, **k):
        return {"smoothed": 61}
    orig = audit_payload.build_stability
    audit_payload.build_stability = _sahte_stability
    try:
        return asyncio.run(audit_payload.build_audit_result_payload(
            domain="geoni.ai", lang="tr",
            crawl_result=crawl or {"total_pages": 5, "pages": [], "ssr": {"js_dependent": True,
                                                                          "hidden_pct": 42}},
            indexing_status=indexing_status or {"indexed_count": 5, "google": 0,
                                                "google_bot_allowed": True},
            brand_recall_result={}, score_result={"overall_score": 61, "breakdown": {}},
            topics={"performing_topics": [], "opportunity_topics": []},
            identity={"name": "GEONI", "topic": "AI"}, **ek))
    finally:
        audit_payload.build_stability = orig


def test_platforms_google_BOT_IZNI_okur_sonuc_sayisini_DEGIL():
    """Kritik regresyon: google=0 (sonuc sayisi) ama izin var -> True olmali."""
    p = _payload({"indexed_count": 5, "google": 0, "google_bot_allowed": True})
    assert p["platforms"]["google"] is True
    p = _payload({"indexed_count": 5, "google": 42, "google_bot_allowed": False})
    assert p["platforms"]["google"] is False


def test_platforms_uc_alan_da_BOOLEAN():
    """Arayuz dordunu de `!platforms.x` ile okuyor; biri int olursa yanlis bulgu."""
    p = _payload()
    for k, v in p["platforms"].items():
        assert isinstance(v, bool), f"platforms.{k} boolean degil: {v!r}"


def test_yeni_alanlar_payload_a_TASINIYOR():
    """ssr/citability/page_type_gap sessizce dusmemeli (olu ozellik olurlar)."""
    p = _payload()
    for k in ("ssr", "citability", "page_type_gap", "sitemap_found", "site_assets"):
        assert k in p, f"{k} payload'dan dusmus"
    assert p["ssr"]["hidden_pct"] == 42


def test_izleme_taramasi_AYNI_alanlari_alir():
    """auto_monitor yalnizca BIR rozet ekler; alan kumesi birebir ayni kalmali."""
    normal = set(_payload().keys())
    izleme = set(_payload(auto_monitor=True).keys())
    assert izleme - normal == {"auto_monitor"}
    assert normal - izleme == set()


def test_model_results_TOP_LEVEL_da_var():
    """self_improve.py:440,456 yalniz top-level okur; ic-ice tek kopya birakilirsa
    motor-kalitesi sinyalleri web taramalarinda sessizce bos kalir."""
    p = _payload()
    assert "model_results" in p
    assert "model_results" in p["brand_recall"]


# ---------- yapisal kalkan: kopya payload geri gelmesin ----------

def _src(ad):
    return (pathlib.Path(__file__).parent.parent / ad).read_text(encoding="utf-8")


def test_cagiranlar_payload_i_ELLE_KURMAZ():
    """
    Kok neden kopyalamaydi. Bu test kopyayi commit aninda yakalar: iki dosyada da
    `"platforms": {` blogu OLMAMALI — payload tek yerde kurulur.
    """
    for ad in ("main.py", "monitor.py"):
        assert '"platforms": {' not in _src(ad), \
            f"{ad} payload'i yine elle kuruyor — sapma riski geri geldi"
        assert "build_audit_result_payload" in _src(ad), \
            f"{ad} ortak insa fonksiyonunu kullanmiyor"


def test_eski_hatali_satir_geri_gelmedi():
    src = _src("audit_payload.py")
    assert '"google": indexing_status.get("google", 0)' not in src, \
        "platforms.google yine SONUC SAYISINI okuyor — yanlis bulgu geri geldi"


# ---------- golden sozlesme: istemcinin okudugu alan sessizce dusmesin ----------

def test_web_client_anahtarlari_payload_da_VAR():
    """
    Marka akisindaki ayni kalkan (BRAND_CLIENT_KEYS) artik WEB akisinda da var.
    Bu sinifin bug'i "mantik hatasi" degil SOZLESME KAYMASIDIR: hicbir sey
    kirilmadan ozellik sessizce olur. Liste olcumle cikarildi (bkz.
    result_contract.WEB_CLIENT_KEYS yorumu).
    """
    from result_contract import WEB_CLIENT_KEYS
    eksik = WEB_CLIENT_KEYS - set(_payload().keys())
    assert not eksik, f"payload'dan dusen istemci anahtarlari: {eksik}"


def test_sozlesme_ile_payload_ARASINDA_kacak_yok():
    """
    Ters yon: payload'a yeni alan eklenip iki listeye de yazilmazsa yakalanir.
    Amac burokrasi degil GORUNURLUK — "kimse okumuyorsa neden uretiyoruz"
    sorusu commit aninda sorulsun (citability tam da boyle kacmisti).
    """
    from result_contract import WEB_CLIENT_KEYS, WEB_INTERNAL_KEYS
    bilinen = WEB_CLIENT_KEYS | WEB_INTERNAL_KEYS | {"auto_monitor"}
    kacak = set(_payload(auto_monitor=True).keys()) - bilinen
    assert not kacak, (
        f"payload'da siniflandirilmamis alan(lar): {kacak}. "
        "Istemci okuyorsa WEB_CLIENT_KEYS'e, okumuyorsa WEB_INTERNAL_KEYS'e ekle.")


# ---------- iki normalize_domain tuzagi (2026-08-03 canlida olculdu) ----------

def test_IKI_normalize_domain_FARKLI_davranir():
    """
    🪤 YASANDI: main.py'de cikPlak `normalize_domain` adi CRAWLER'inkine bagli
    (main.py:26), db'ninkine degil. Submit yolunda yanlisini cagirdim ve canli
    olcumde audits.domain "www.geoni.ai" olarak kaydedildi — ne ham string ne de
    beklenen "geoni.ai".

    Bu test iki fonksiyonun AYNI OLMADIGINI belgeler; birleştirilirlerse
    kirmizi yanar ve o zaman main.py'deki uyari yorumu da guncellenmeli.
    """
    import crawler
    from db import normalize_domain as db_normalize
    ham = "https://www.geoni.ai/rehber"
    assert crawler.normalize_domain(ham) == "www.geoni.ai"   # www KALIR
    assert db_normalize(ham) == "geoni.ai"                    # www SOYULUR


def test_submit_yolu_WWW_SOYAN_normalize_kullanir():
    """
    Kaynak seviyesinde: main.py fastapi ister, CI minimal ortaminda import
    edilemez. "www." soyulmazsa domain'e gore gruplanan HER SEY bolunur —
    lig satiri, stability trend gecmisi ve bilet on-kosulu (purchase_ticket
    db.normalize_domain ile "geoni.ai" arar, "www.geoni.ai" kaydini bulamaz).
    """
    src = _src("main.py")
    assert "temiz_domain = _valid_domain(request.domain)" in src, \
        "submit yolu dogrulamanin dondurdugu temiz degeri kullanmali"
    assert "request.domain = temiz_domain" in src
    assert "request.domain = normalize_domain(request.domain)" not in src, \
        "CRAWLER'in normalize_domain'i submit yolunda kullanilmis — www kalir"
