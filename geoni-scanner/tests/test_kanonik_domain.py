"""audits.domain TEK kanonik biçimde yazılır — geçmiş ikiye bölünmesin.

NE OLDU (2026-08-06, canlı veriyle ölçüldü):
`create_pending_audit` çağrılırken kullanıcının YAZDIĞI ham metin doğrudan
kaydediliyordu. Aynı sitenin geçmişi ikiye bölünmüştü:

    geoni.ai            + www.geoni.ai        -> 67 tarama, iki ayrı seri
    cagricakir.com.tr   + Cagricakir.com.tr   -> 52 tarama, iki ayrı seri

Bu bölünme aynı anda dört şeyi bozuyordu: trend grafiği, `apply_audit_retention`
gruplaması, `content_decay` kıyası ve izleme listesi eşleşmesi.

🪤 Kod tabanında İKİ `normalize_domain` var: `db`'deki `www`'yi SIYIRIR,
`crawler`'daki BIRAKIR. Kanonik biçim için `db`'deki kullanılır.
"""
import db


def test_www_siyrilir():
    assert db.kanonik_domain("www.geoni.ai") == "geoni.ai"
    assert db.kanonik_domain("geoni.ai") == "geoni.ai"


def test_buyuk_harf_kucultulur():
    assert db.kanonik_domain("Cagricakir.com.tr") == "cagricakir.com.tr"
    assert db.kanonik_domain("GEONI.AI") == "geoni.ai"


def test_ayni_siteyi_TEK_ANAHTARA_indirger():
    """Asıl dava: dört farklı yazım, tek anahtar."""
    yazimlar = ["geoni.ai", "www.geoni.ai", "https://geoni.ai/", "  WWW.Geoni.AI  "]
    assert len({db.kanonik_domain(y) for y in yazimlar}) == 1


def test_semа_ve_yol_siyrilir():
    assert db.kanonik_domain("https://geoni.ai/rehber") == "geoni.ai"
    assert db.kanonik_domain("http://www.geoni.ai/a/b?c=1") == "geoni.ai"


def test_alan_adi_OLMAYAN_hedef_satiri_bozmaz():
    """brand/person taramalarının `domain` alanı da bu yoldan geçebiliyor.
    normalize None dönse bile satır oluşmaya DEVAM etmeli — yalnız kırpılır."""
    assert db.kanonik_domain("Sabri Çağrı Çakır") == "sabri çağrı çakır"
    assert db.kanonik_domain("@kahveduragi") == "@kahveduragi"
    assert db.kanonik_domain("  Marka Adı  ") == "marka adı"


def test_bos_girdi_patlatmaz():
    assert db.kanonik_domain("") == ""
    assert db.kanonik_domain(None) == ""


def test_iki_normalize_FARKLI_davraniyor_bilerek():
    """🪤 Regresyon kapanı: biri ötekine eşitlenirse burada görünür.
    crawler'ınki `www`'yi BIRAKIR (gerçek istek o adrese gider),
    db'deki SIYIRIR (kanonik anahtar)."""
    import crawler
    assert crawler.normalize_domain("www.geoni.ai") == "www.geoni.ai"
    assert db.normalize_domain("www.geoni.ai") == "geoni.ai"


def test_create_pending_audit_KANONIK_yaziyor(monkeypatch):
    """Uçtan uca: POST gövdesindeki `domain` kanonik olmalı."""
    import asyncio
    yakalanan = {}

    class SahteYanit:
        status_code = 201

    class Sahte:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None, timeout=None):
            yakalanan.update(json)
            return SahteYanit()

    monkeypatch.setattr(db, "SUPABASE_URL", "https://ornek.supabase.co")
    monkeypatch.setattr(db, "SUPABASE_SERVICE_KEY", "test")
    monkeypatch.setattr(db.httpx, "AsyncClient", Sahte)

    ok = asyncio.run(db.create_pending_audit("j1", "web", "WWW.Geoni.AI", "u1"))
    assert ok is True
    assert yakalanan["domain"] == "geoni.ai"
