"""SSR korlugu: AI botlari JS calistirmaz, biz calistiriyoruz (2026-08-02).

YASANDI/OLCULDU: crawler.py Playwright kullaniyor. Icerigi yalnizca JS sonrasi
olusan bir sitede BIZ dolu sayfa goruyor, GPTBot bos kabuk goruyordu. Rapor
"AI erisimi tamam" diyordu — eksik olcum degil, SAHTE GUVEN.
    app.geoni.ai  ->   175 karakter (JS'siz)   = SPA
    geoni.ai      -> 7.818 karakter (JS'siz)   = statik
"""
import asyncio

import scoring
import ssr_check


# ---------- gorunen metin cikarimi ----------

def test_script_ve_style_metin_sayilmaz():
    h = "<html><head><style>body{color:red}</style><script>var a=1</script></head><body>Merhaba dunya</body></html>"
    assert ssr_check.gorunen_metin(h) == "Merhaba dunya"


def test_HTML_YORUMU_metin_sayilmaz():
    """
    Asil tuzak: app.geoni.ai olcumunde 'gorunen' 175 karakterin buyuk kismi bir
    performans NOTUYDU (HTML yorumu). Yorum sayilsaydi bos bir SPA'yi
    'icerigi var' diye olcerdik.
    """
    h = "<html><body><!-- CSS->font zinciri LCP'ye 600ms ekliyordu, preconnect eklendi -->Ana</body></html>"
    assert ssr_check.gorunen_metin(h) == "Ana"


def test_bos_girdi_patlamaz():
    assert ssr_check.gorunen_metin("") == ""
    assert ssr_check.gorunen_metin(None) == ""


# ---------- oran / karar ----------

def _sahte_getir(monkeypatch, ham_uzunluklar: dict):
    """safe_get'i ag'a cikmadan taklit eder: url -> ham HTML metni."""
    class Resp:
        def __init__(self, t): self.status_code = 200; self.text = t

    async def sahte(client, url, **kw):
        return Resp("<html><body>" + ("x " * ham_uzunluklar[url]) + "</body></html>")

    monkeypatch.setattr(ssr_check, "safe_get", sahte)


def test_statik_site_js_bagimli_SAYILMAZ(monkeypatch):
    # ham ~ render: klasik statik sayfa
    _sahte_getir(monkeypatch, {"https://a.com/": 500})
    out = asyncio.run(ssr_check.check_ssr([{"url": "https://a.com/", "text_len": 1000}]))
    assert out["js_dependent"] is False
    assert out["median_ratio"] >= 0.9


def test_spa_js_bagimli_SAYILIR(monkeypatch):
    # ham metin neredeyse yok, render dolu -> SPA
    _sahte_getir(monkeypatch, {"https://spa.com/": 5})
    out = asyncio.run(ssr_check.check_ssr([{"url": "https://spa.com/", "text_len": 5000}]))
    assert out["js_dependent"] is True
    assert out["hidden_pct"] >= 90


def test_medyan_kullanilir_tek_aykiri_sayfa_savurmaz(monkeypatch):
    """Bir sayfanin bos olmasi tum siteyi 'SPA' ilan etmemeli."""
    urls = {f"https://a.com/{i}": 500 for i in range(4)}
    urls["https://a.com/bos"] = 1          # tek aykiri
    sayfalar = [{"url": u, "text_len": 1000} for u in urls]
    _sahte_getir(monkeypatch, urls)
    out = asyncio.run(ssr_check.check_ssr(sayfalar))
    assert out["js_dependent"] is False, out


def test_ornek_sayisi_sinirli(monkeypatch):
    """50 sayfalik sitede 50 ek istek atmayalim."""
    urls = {f"https://a.com/{i}": 500 for i in range(50)}
    _sahte_getir(monkeypatch, urls)
    out = asyncio.run(ssr_check.check_ssr([{"url": u, "text_len": 1000} for u in urls]))
    assert out["sampled"] == ssr_check.SAMPLE_SIZE


def test_olcum_yapilamazsa_None(monkeypatch):
    assert asyncio.run(ssr_check.check_ssr([])) is None
    assert asyncio.run(ssr_check.check_ssr([{"url": "https://a.com/", "text_len": 0}])) is None


def test_getirme_hatasi_taramayi_dusurmez(monkeypatch):
    async def patlar(client, url, **kw):
        raise RuntimeError("ag yok")
    monkeypatch.setattr(ssr_check, "safe_get", patlar)
    assert asyncio.run(ssr_check.check_ssr([{"url": "https://a.com/", "text_len": 100}])) is None


# ---------- skorlama ----------

def _ai_access(ssr=None):
    idx = {"bot_access": {"arama": {"a": True, "b": True}, "egitim": {"c": True}},
           "llms_txt": True}
    crawl = {"sitemap_found": True}
    if ssr is not None:
        crawl["ssr"] = ssr
    return scoring.compute_ai_access_score(idx, crawl)


def test_ssr_olcumu_yoksa_ceza_yok():
    """Eski taramalar ve olcum hatasi cezalandirilmamali (muhafazakar)."""
    a = _ai_access(None)
    assert a["ssr_penalty"] == 0.0
    assert a["score"] == 100.0


def test_statik_sitede_ceza_yok():
    a = _ai_access({"js_dependent": False, "median_ratio": 0.95, "hidden_pct": 5})
    assert a["ssr_penalty"] == 0.0


def test_spa_cezalandirilir():
    """
    Bot izinleri TAM olsa bile icerik gorunmuyorsa skor dusmeli — duzeltmenin
    butun amaci bu.
    """
    tam = _ai_access(None)["score"]
    spa = _ai_access({"js_dependent": True, "median_ratio": 0.02, "hidden_pct": 98})
    assert spa["score"] < tam
    assert spa["ssr_penalty"] > 30          # 40 * 0.98
    assert spa["ssr_hidden_pct"] == 98


def test_ceza_skoru_negatife_dusurmez():
    idx = {"bot_access": {"arama": {"a": False}, "egitim": {"c": False}}, "llms_txt": False}
    a = scoring.compute_ai_access_score(
        idx, {"sitemap_found": False, "ssr": {"js_dependent": True, "median_ratio": 0.0}})
    assert a["score"] >= 0.0


def test_bot_korumasi_JS_sanilmaz():
    """
    🪤 Olculdu 2026-08-02: seoyen.com GPTBot'a 403 "Your request was blocked."
    (25 bayt), tarayiciya 200 (178 KB). Ham metin bos gelince sayfa "JS-bagimli"
    gibi gorunur — sebep TAMAMEN BASKA. Engelleme suphesi varsa SSR cezasi
    uygulanmamali: engelleme zaten arama/egitim oranlariyla cezalandiriliyor,
    ikinci ceza + YANLIS SEBEP gostermek olur.
    """
    idx = {"bot_access": {"arama": {"a": True}, "egitim": {"c": True}},
           "llms_txt": True, "bot_protection_suspected": True}
    crawl = {"sitemap_found": True,
             "ssr": {"js_dependent": True, "median_ratio": 0.01, "hidden_pct": 99}}
    a = scoring.compute_ai_access_score(idx, crawl)
    assert a["ssr_penalty"] == 0.0, "bot korumasi varken SSR cezasi verilmemeli"


def test_403_olcumden_atilir(monkeypatch):
    """Engellenen sayfa oran hesabina HIC girmemeli (paydayi bozar)."""
    class Resp:
        status_code = 403
        text = "Your request was blocked."
    async def sahte(client, url, **kw):
        return Resp()
    monkeypatch.setattr(ssr_check, "safe_get", sahte)
    assert asyncio.run(ssr_check.check_ssr([{"url": "https://x.com/", "text_len": 5000}])) is None


# ---------- SSRF / DNS-rebind kapisi (2026-08-02 guvenlik denetimi) ----------

def test_ic_adrese_giden_sayfa_AG_ISTEGI_ATMAZ(monkeypatch):
    """
    🪤 Bu modul crawl BITTIKTEN sonra (crawler.py:40 — 90 sn'ye kadar) ayni
    URL'lere YENI bir httpx baglantisiyla gider. crawler.py host'u uc katmanda
    dogruluyor ama o dogrulamalar bu istegi KAPSAMAZ; `safe_get` ise yalniz
    redirect hop'larini dogrular, ILK istegi degil (ssrf_guard.py:98). Aradaki
    surede saldirgan kendi domaininin DNS'ini ic bir IP'ye cevirirse istek ic
    aga giderdi. Guard'in istegin ONUNDE oldugunu kanitlar: safe_get cagrilirsa
    test patlar (yalnizca "None dondu" kaniti yetmez).
    """
    cagrildi = []

    async def _patlat(*a, **k):
        cagrildi.append(a)
        raise AssertionError("safe_get ic hedefe cagrildi — SSRF acik")

    monkeypatch.setattr(ssr_check, "safe_get", _patlat)
    assert asyncio.run(ssr_check._tek_sayfa(None, "https://169.254.170.2/x", 100)) is None
    assert cagrildi == []
