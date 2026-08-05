"""v5: "olculemedi" != "yok" — crawl 0 sayfa dondugunde skor sifirlanmamali.

NE OLDU (14 Temmuz'da onaylandi, 5 Agustos'ta uygulandi):
Bot korumasi crawl'i kestiginde `pages` bos geliyordu ve
  · `_indexability_score([])` -> 0.0   ("hicbir sayfan indekslenebilir degil")
  · `compute_schema_score([])` -> 0.0  ("schema yok")
yaziliyordu. Yani ERISEMEDIGIMIZ site, o boyutlarda SIFIR ALMIS gibi puanlaniyordu.

OLCUM (60 gun, 103 web taramasi): 36'si (%35) sifir sayfayla bitmis. Bu
taramalarda authority 81,7 ve ai_access 82,6 — yani siteler kotu degil, biz
gezemedik. Buna ragmen index_coverage 0,7 ve schema 0,0 cikmis; toplam skor
ortalamasi 37,6 (normal taramalarda 62,5). Farkin ~19 puani olcum artefaktiydi.

KURAL: olculemeyen boyut formulden DUSER ve agirligi kalan boyutlara ORANTILI
dagitilir. Bu desen kodda zaten vardi (brand_recall checked=False iken 6->5
boyut); v5'te butun boyutlara genellestirildi.
"""
import scoring


def _sayfalar(n=4):
    return [{"url": f"https://x.com/{i}", "canonical_url": f"https://x.com/{i}"} for i in range(n)]


# ── Bilesen seviyesi ────────────────────────────────────────────────────────

def test_indexability_sayfa_yoksa_none_doner_sifir_degil():
    """Regresyon capasi: birisi tekrar 0.0'a cevirirse burada patlasin."""
    assert scoring._indexability_score([]) is None
    assert scoring._indexability_score(_sayfalar()) == 100.0


def test_index_coverage_sayfa_yok_brave_yok_olculemedi():
    res = scoring.compute_index_coverage({"domain": "x.com", "pages": []}, {"indexed_count": 0})
    assert res["measured"] is False
    assert res["score"] is None
    assert res["indexability"] is None


def test_index_coverage_sayfa_yok_ama_brave_varsa_o_sinyale_dayanir():
    """Crawl kesilse de Brave indeksi gercek bir sinyal — atmiyoruz."""
    res = scoring.compute_index_coverage({"domain": "x.com", "pages": []},
                                         {"indexed_count": 0, "brave_indexed": True})
    assert res["measured"] is True
    assert res["score"] == 100.0


def test_schema_sayfa_yoksa_olculemedi_sifir_degil():
    res = scoring.compute_schema_score([])
    assert res["measured"] is False
    assert res["score"] is None
    res2 = scoring.compute_schema_score(_sayfalar())
    assert res2["measured"] is True


def test_freshness_sayfa_yoksa_zaten_notr_kaliyor():
    """Bu boyut ZATEN dogru davraniyordu (notr 50) — degistirmedik, kilitliyoruz."""
    assert scoring.compute_freshness_score([], None)["score"] == 50.0


# ── Formul seviyesi ─────────────────────────────────────────────────────────

async def _skor(pages, monkeypatch, brave=None, brand=60.0):
    """Ag cagrisi yok: authority sahte. Digerleri saf fonksiyon."""
    async def sahte_authority(domain, brand_name="", web_results=None):
        return {"score": 80.0, "legs": {}, "kg": None}
    monkeypatch.setattr(scoring, "estimate_authority_score", sahte_authority)
    status = {"indexed_count": 0, "robots": {}}
    if brave is not None:
        status["brave_indexed"] = brave
    return await scoring.compute_ai_visibility_score(
        {"domain": "x.com", "pages": pages}, status,
        {"checked": True, "score": brand, "name": "X", "web_results": []})


def test_olculemeyen_boyut_kirilimda_YER_ALMAZ(monkeypatch):
    """Frontend eksik anahtari `?? 100` ile karsiliyor -> 'oneri yok' olur.
    0 yazsaydik kullaniciya 'schema'n sifir' diye YANLIS tavsiye verirdik."""
    import asyncio
    r = asyncio.run(_skor([], monkeypatch))
    assert "schema" not in r["breakdown"]
    assert "index_coverage" not in r["breakdown"]
    assert "authority" in r["breakdown"]          # crawl'a bagli olmayanlar durur
    assert set(r["diagnostics"]["unmeasured_dimensions"]) == {"schema", "index_coverage"}
    assert r["diagnostics"]["low_confidence"] is True


def test_agirliklar_yeniden_dagitiliyor_toplam_bir(monkeypatch):
    import asyncio
    r = asyncio.run(_skor([], monkeypatch))
    assert abs(sum(r["weights_used"].values()) - 1.0) < 1e-6
    assert "schema" not in r["weights_used"]


def test_erisilemeyen_site_artik_cezalandirilmiyor(monkeypatch):
    """Asil dava: ayni sinyallerle, crawl kesildiginde skor COKMEMELI."""
    import asyncio
    kesik = asyncio.run(_skor([], monkeypatch))["overall_score"]
    # Eski davranista index_coverage=0 + schema=0 sabit agirlikla giriyordu:
    eski = (0 * 0.20) + (80 * 0.15) + (50 * 0.10) + (0 * 0.10) + \
           (asyncio.run(_skor([], monkeypatch))["breakdown"]["ai_access"] * 0.12) + \
           (asyncio.run(_skor([], monkeypatch))["breakdown"]["engagement"] * 0.08) + (60 * 0.25)
    assert kesik > eski, f"v5 skoru ({kesik}) eski hesaptan ({eski:.1f}) yuksek olmali"


def test_crawl_BASARILIYSA_davranis_degismiyor(monkeypatch):
    """Kritik: duzeltme yalniz 0-sayfa dalini etkiler. Sayfa varsa tum
    boyutlar olculur, agirliklar orijinal degerlerinde kalir."""
    import asyncio
    r = asyncio.run(_skor(_sayfalar(), monkeypatch))
    assert r["diagnostics"]["unmeasured_dimensions"] == []
    assert r["diagnostics"]["low_confidence"] is False
    assert r["weights_used"]["brand_recall"] == 0.25
    assert r["weights_used"]["index_coverage"] == 0.20
    assert r["weights_used"]["schema"] == 0.10


def test_surum_v5_ve_lig_sabiti_ESLESIYOR():
    """🪤 scoring.py ve db.py'deki iki sabit ELLE eslenmis (import dongusu yok).
    Biri guncellenip digeri unutulursa lig SESSIZCE bosalir."""
    import db
    assert scoring.SCORING_VERSION == db.SCORING_VERSION_SHOWN


# ── Lig = Türkiye vitrini (kurucu kararı, 2026-08-05) ───────────────────────
# Karar geoni-open-items'ta "sonra TR-only vitrin" diye yazılıydı ama koda hiç
# girmemişti; v5 yeniden taramasında lig Vercel/Netlify/Heroku ile doldu ve
# kurucu fark etti. Bu testler süzgecin bir daha kaybolmamasını sağlar.

def test_lig_yabanci_siteleri_eler():
    import db
    for yabanci in ("vercel.com", "netlify.com", "heroku.com", "cloudflare.com",
                    "nuxt.com", "stripe.com", "figma.com", "deno.com"):
        assert db.tr_sitesi_mi(yabanci) is False, f"{yabanci} vitrine girmemeli"


def test_lig_tr_uzantiyi_kabul_eder():
    import db
    for tr in ("cagricakir.com.tr", "turkiye.gov.tr", "ornek.org.tr", "site.tr"):
        assert db.tr_sitesi_mi(tr) is True, f"{tr} vitrine girmeli"


def test_lig_jenerik_uzantili_turk_markalarini_kabul_eder():
    """.com'lu Türk markaları TLD'den anlaşılmıyor — kürator listesinden gelir."""
    import db
    for marka in ("armut.com", "trendyol.com", "webtures.com", "geoni.ai"):
        assert db.tr_sitesi_mi(marka) is True, f"{marka} vitrine girmeli"


def test_tr_suzgeci_bos_ve_bozuk_girdide_patlamaz():
    import db
    assert db.tr_sitesi_mi("") is False
    assert db.tr_sitesi_mi(None) is False
    assert db.tr_sitesi_mi("  VERCEL.COM  ") is False
    assert db.tr_sitesi_mi("  ARMUT.COM ") is True     # buyuk harf + bosluk
    assert db.tr_sitesi_mi("example.str") is False     # ".tr" ekiyle karismasin
