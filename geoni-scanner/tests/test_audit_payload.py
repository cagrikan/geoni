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
    # KALICI COZUM: ad main.py namespace'ine HIC girmesin ki yanlisi
    # cagrilamasin. Bu satir geri gelirse tuzak da geri gelmis demektir.
    assert "from crawler import crawl_domain, normalize_domain" not in src, \
        "crawler.normalize_domain main.py'ye yeniden import edilmis — ad tuzagi geri geldi"


# ---------- kirilim bulgulari: esik paritesi (2026-08-03) ----------

def test_KIRILIM_ESIKLERI_web_ve_mobil_AYNI():
    """
    🎯 Dort boyut (index_coverage/authority/freshness/engagement) cikPlak sayi
    olarak duruyordu — teshis vardi, tedavi yoktu. Artik ikisinde de bulgu
    uretiyor. Esikler AYRI iki dosyada yaziliyor; sapmasinlar diye burada
    karsilastirilir (ayni bulgu iki platformda farkli tetiklenirse kullanici
    "mobilde vardi webde yok" der).
    """
    import pathlib, re
    kok = pathlib.Path(__file__).parent.parent.parent.parent  # ~/
    web = (kok / "geoni-frontend/src/ResultsPage.jsx")
    mob = (kok / "geoni-mobile/app/result/[jobId].tsx")
    if not (web.exists() and mob.exists()):
        import pytest
        pytest.skip("istemci depolari bu ortamda yok (CI backend-only)")

    w = web.read_text(encoding="utf-8")
    m = mob.read_text(encoding="utf-8")
    for alan, esik in (("index_coverage", 50), ("authority", 40),
                       ("freshness", 50), ("engagement", 40)):
        assert re.search(rf"score_breakdown\.{alan} \?\? 100\) < {esik}", w), \
            f"web'de {alan} esigi {esik} degil"
        assert re.search(rf"'{alan}', esik: {esik}", m), \
            f"mobilde {alan} esigi {esik} degil"


# ---------- ozel tarama: soz TUTULUYOR mu (2026-08-03) ----------

def test_OZEL_TARAMA_sonucu_isaretleniyor_ve_siliniyor():
    """
    🪤 YASANDI: musteriye "sonuc hicbir yerde kaydedilmedi" diyorduk ama SQS
    modunda (uretimde ACIK) sonuc, polling okuyabilsin diye audits satirina
    yaziliyor ve orada KALIYORDU. Soz tutulmuyordu. O gune kadar hic ozel
    tarama satin alinmamisti (olculdu) — kimse yanilmadi, ama duzeltildi.

    Uc parca birlikte calismali; biri dusrese soz yine bozulur:
      1) sonuc `private` isaretiyle yazilir (satirda private'i gosteren baska
         alan YOK: user_id ozel ve anonim taramada ayni sekilde bostur)
      2) teslimde silinir
      3) hic pollenmeyenler icin supurge var
    """
    src = _src("main.py")
    assert 'result_payload["private"] = True' in src, "ozel sonuc isaretlenmiyor"
    assert "await purge_private_result(job_id)" in src, "teslimde silme yok"
    assert '"ozel_tarama_silindi"' in src, "silinmis sonuc icin acik cevap yok"

    db = _src("db.py")
    assert "async def purge_private_result" in db
    assert "async def sweep_private_results" in db

    mon = _src("monitor.py")
    assert "sweep_private_results()" in mon, \
        "supurge gunluk ise baglanmamis — sekmeyi kapatan kullanici icin soz tutulmaz"


def test_SILINMIS_sonuc_icin_MUSTERI_METNI_var():
    """410 + aciklayici metin: 'tarama basarisiz' demek kullaniciyi yaniltirdi.
    Metin ayrica kopyasinin NEREDE oldugunu soylemeli (e-posta)."""
    import api_errors
    kod = api_errors.MESAJLAR["ozel_tarama_silindi"]
    assert "e-posta" in kod["tr"].lower() and "email" in kod["en"].lower()


# ---------- ic dogrulama taramasi: ucuz AMA lige girmez (2026-08-03) ----------

def test_IC_DOGRULAMA_damgasi_ve_LIG_disi():
    """
    Ic dogrulama taramasinda SOV atlanir (maliyetin ~%65'i) — ama bu SKORU
    DEGISTIRIR. O yuzden damga SART: damgasiz kalirsa eksik skorlu bir kayit
    herkese acik lige sizar.
    """
    p = _payload(auto_monitor=False)
    assert "internal" not in p, "normal taramada damga OLMAMALI"
    pi = _payload(ic_dogrulama=True)
    assert pi.get("internal") is True

    db = _src("db.py")
    assert 'internal:result_json->>internal' in db, "lig sorgusu alani cekmiyor"
    assert 'if row.get("internal"):' in db, "lig ic taramalari elemiyor"


def test_IC_DOGRULAMA_bayragi_PUBLIC_API_de_DEGIL():
    """
    🔒 Bayrak AuditRequest'e KONMAMALI: konsaydi istemci kendi gonderip
    SOV'suz (farkli skorlu) tarama uretebilirdi. Yalnizca dogrulanmis
    X-Internal-Scan basligindan set edilir ve kuyruk mesajinda tasinir.
    """
    src = _src("main.py")
    assert "ic_dogrulama: Optional[bool]" not in src, "bayrak public modele sizmis"
    assert '"ic_dogrulama": _ic_dogrulama_taramasi(http_request)' in src
    assert "X-Internal-SOV" in src, "SOV'u geri acan kacis kapisi yok"


def test_SOV_ATLAMA_yalniz_bayrakla():
    """need_sov varsayilani True kalmali; yanlislikla kapanirsa TUM musteri
    taramalari SOV'suz kalir ve skorlar sessizce degisir."""
    br = _src("brand_recall.py")
    assert "need_sov: bool = True," in br
    assert "need_sov=not ic_dogrulama" in _src("main.py")


# ---------- Claude SOV motoru kapali, judge acik (2026-08-03) ----------

def test_CLAUDE_SOV_MOTORU_KAPALI_ama_JUDGE_ACIK():
    """
    Kurucu karari: Claude'un SOV web-arama motoru kapatildi (Turkce kaynak
    derinligi en dusuk, maliyeti Perplexity'nin 3,7 kati) — ama Claude MODELI
    judge olarak KALDI. Ikisi karisirsa ya para bosa gider ya judge coker.
    """
    br = _src("brand_recall.py")
    # SOV motoru env-kapili
    assert 'os.environ.get("CLAUDE_SOV")' in br, "Claude SOV motoru kapali degil"
    assert "ask_claude_web=(_ask_claude_web" in br
    # judge DOKUNULMADI
    assert 'JUDGE_MODEL_ANTHROPIC = "claude-sonnet-4-6"' in br, \
        "judge modeli degismis — Claude judge olarak KALMALI"
    assert "model=JUDGE_MODEL_ANTHROPIC" in br, "judge cagrisi kopmus"


def test_SOV_MOTORLARI_ucu_acik():
    """ChatGPT + Perplexity + Gemini acik kalmali; biri sessizce duserse
    SOV paydasi kucuulur ve skorlar sessizce kayar."""
    br = _src("brand_recall.py")
    assert "ask_openai_web=_ask_openai_web if OPENAI_API_KEY else None" in br
    assert "ask_google=_ask_gemini_grounded if GOOGLE_API_KEY else None" in br
