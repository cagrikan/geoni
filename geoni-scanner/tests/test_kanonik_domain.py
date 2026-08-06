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


def _post_yakalayici(monkeypatch):
    """save_audit'in POST gövdesini yakalar."""
    yakalanan = {}

    class SahteYanit:
        status_code = 201
        text = ""

    class Sahte:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None, timeout=None):
            yakalanan.update(json)
            return SahteYanit()

        async def patch(self, url, headers=None, json=None, timeout=None):
            return SahteYanit()

    monkeypatch.setattr(db, "SUPABASE_URL", "https://ornek.supabase.co")
    monkeypatch.setattr(db, "SUPABASE_SERVICE_KEY", "test")
    monkeypatch.setattr(db.httpx, "AsyncClient", Sahte)
    return yakalanan


def test_save_audit_de_KANONIK_yaziyor(monkeypatch):
    """🪤 İKİNCİ yazma noktası. İzleme (monitor) yolu `create_pending_audit`'ten
    DEĞİL buradan geçiyor: `monitor._scan_web_item` -> `save_audit`. İlk
    düzeltmede atlanmıştı; yalnız birini kanonikleştirmek bölünmeyi SÜRDÜRÜR
    (izleme listesindeki hedef `Cagricakir.com.tr` yazımıyla duruyor)."""
    import asyncio
    yakalanan = _post_yakalayici(monkeypatch)
    asyncio.run(db.save_audit("j2", {"domain": "Cagricakir.com.tr", "email": ""},
                              {"score": 71}, user_id=None, deduct=False))
    assert yakalanan["domain"] == "cagricakir.com.tr"


def test_save_audit_www_li_izleme_hedefi_TEK_seriye_yazar(monkeypatch):
    """İzleme `www.geoni.ai` yazımıyla dursa bile audit satırı `geoni.ai` olur."""
    import asyncio
    yakalanan = _post_yakalayici(monkeypatch)
    asyncio.run(db.save_audit("j3", {"domain": "www.geoni.ai"}, {"score": 80}))
    assert yakalanan["domain"] == "geoni.ai"


def test_HER_audits_yazma_noktasi_kanonikten_geciyor():
    """🪤 Regresyon kapanı: `audits` satırına ham `domain` yazan yeni bir yol
    eklenirse burada patlar. Yalnız YAZMA yolları sayılır — `params={"domain":
    f"eq.{...}"}` bir okuma süzgeci, `{"domain": d}` ise yanıt sözlüğü."""
    import re
    from pathlib import Path
    kaynak = (Path(__file__).resolve().parent.parent / "db.py").read_text(encoding="utf-8")
    ham = re.findall(r'"domain":\s*(request_data|domain\b|ham\b)', kaynak)
    assert not ham, f"kanonikten geçmeyen domain yazımı: {ham}"
    # iki bilinen yazma noktası da yerinde mi
    assert kaynak.count('"domain": kanonik_domain(') == 2


def _get_yakalayici(monkeypatch, bulunanlar: dict):
    """GET süzgecini yakalar; `bulunanlar[domain]` varsa o satırı döner."""
    sorulanlar = []

    class SahteYanit:
        def __init__(self, veri):
            self._veri = veri
            self.status_code = 200

        def json(self):
            return self._veri

    class Sahte:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, headers=None, timeout=None):
            d = (params or {}).get("domain", "").removeprefix("eq.")
            sorulanlar.append(d)
            satir = bulunanlar.get(d)
            return SahteYanit([satir] if satir else [])

    monkeypatch.setattr(db, "SUPABASE_URL", "https://ornek.supabase.co")
    monkeypatch.setattr(db, "SUPABASE_SERVICE_KEY", "test")
    monkeypatch.setattr(db.httpx, "AsyncClient", Sahte)
    return sorulanlar


def test_okuma_KANONIK_bicimle_bulur(monkeypatch):
    """Bilet hedefi `www.geoni.ai` yazılmış; satır `geoni.ai` olarak duruyor."""
    import asyncio
    sorulanlar = _get_yakalayici(monkeypatch, {"geoni.ai": {"id": "a1"}})
    sonuc = asyncio.run(db.get_latest_web_audit_by_domain("www.geoni.ai"))
    assert sonuc == {"id": "a1"}
    assert sorulanlar[0] == "geoni.ai"          # önce kanonik denendi


def test_okuma_kanonik_yoksa_HAM_bicime_duser(monkeypatch):
    """Eski satırlar ham yazılmıştı — geriye düşüş olmazsa geçmiş kaybolur."""
    import asyncio
    sorulanlar = _get_yakalayici(monkeypatch, {"WWW.Geoni.AI": {"id": "eski"}})
    sonuc = asyncio.run(db.get_latest_web_audit_by_domain("WWW.Geoni.AI"))
    assert sonuc == {"id": "eski"}
    assert sorulanlar == ["geoni.ai", "WWW.Geoni.AI"]


def test_okuma_zaten_kanonikse_TEK_sorgu(monkeypatch):
    """🪤 Sonsuz özyineleme/çift sorgu kapanı."""
    import asyncio
    sorulanlar = _get_yakalayici(monkeypatch, {"geoni.ai": {"id": "a1"}})
    asyncio.run(db.get_latest_web_audit_by_domain("geoni.ai"))
    assert sorulanlar == ["geoni.ai"]
