"""grok_web günlük tavanı: kontrol HER ÇAĞRIDA, tarama başına değil.

NE OLDU (2026-08-07'de canlı veriyle ölçüldü): tavan `GROK_WEB_DAILY_CAP=5`
olmasına rağmen iki gün aşıldı —

    5 Ağustos: 10 çağrı   ·   2 Ağustos: 9 çağrı   ·   diğer tüm günler: tam 5

Tam iki katı olması tesadüf değil. Kontrol tarama başlangıcında **bir kez**
yapılıyor, sonra `_ask_grok_web` olduğu gibi SOV'a geçiliyordu; SOV ise her
SORGU için ayrı çağırıyor (~5 sorgu = 5 çağrı). İki tarama eş zamanlı
başlayınca (`SCAN_CONCURRENCY=2`) ikisi de sayacı 0 görüp geçiyor.

Para etkisi: grok_web ~$0,10/çağrı → günlük ~$0,50 yerine ~$1,00. Grok bakiyesi
7 Ağustos'ta $2,08'e inmişti; bu sızıntı ömrü kısaltıyordu.

🪤 Bu testler `brand_recall`'ı IMPORT ETMEZ — modül httpx/anthropic gibi ağır
bağımlılıklar çekiyor ve CI'ın asgari ortamında toplama aşamasında düşerdi
(bu gece `test_card.py` ile aynen yaşandı). Politika KAYNAK OKUNARAK kilitlenir.
"""
import re
from pathlib import Path

KAYNAK = (Path(__file__).resolve().parent.parent / "brand_recall.py").read_text(encoding="utf-8")
BLOK = KAYNAK[KAYNAK.index('if GROK_API_KEY and os.environ.get("GROK_WEB_SHADOW")'):][:2600]


def test_sarmalayici_VAR():
    """Ham `_ask_grok_web` doğrudan geçilirse tavan tarama başına kalır."""
    assert "_grok_web_tavanli" in BLOK, "tavan sarmalayıcısı yok"


def test_sarmalayici_HER_CAGRIDA_sayaci_okuyor():
    """Asıl koruma: fonksiyonun İÇİNDE sayaç kontrolü olmalı."""
    i = BLOK.index("async def _grok_web_tavanli")
    govde = BLOK[i:i + 400]
    assert "count_provider_calls_today" in govde
    assert ">= _cap" in govde
    assert "return None" in govde, "tavan dolunca sessizce atlanmalı"


def test_tavan_dolunca_PATLAMAZ_sessizce_atlar():
    """🪤 Gölge-mod: grok_web yokluğu canlı skoru DEĞİŞTİRMEZ. İstisna fırlatmak
    tarama akışını bozardı — `return None` şart."""
    i = BLOK.index("async def _grok_web_tavanli")
    govde = BLOK[i:i + 400]
    assert "raise" not in govde


def test_cap_sifir_ise_SINIRSIZ():
    """`GROK_WEB_DAILY_CAP=0` bilinçli kapatma: sarmalayıcı devreye girmez."""
    assert "if _cap <= 0:" in BLOK
    i = BLOK.index("if _cap <= 0:")
    assert "grok_web_fn_ = _ask_grok_web" in BLOK[i:i + 120]


def test_varsayilan_tavan_5():
    m = re.search(r'GROK_WEB_DAILY_CAP", "(\d+)"', BLOK)
    assert m and m.group(1) == "5"


def test_gerekce_kaynakta_YAZILI():
    """Ölçüm silinirse biri 'bu sarmalayıcı fazladan sorgu atıyor' deyip kaldırır."""
    assert "SCAN_CONCURRENCY" in BLOK
    assert "10 cagri" in BLOK or "10 çağrı" in BLOK


def test_ham_fonksiyon_SOV_a_dogrudan_gecmiyor():
    """🪤 Regresyon kapanı: `grok_web_fn_ = _ask_grok_web` yalnız cap<=0 dalında
    olmalı. Tavanlı dalda ham fonksiyon geçerse hata geri gelir."""
    atamalar = re.findall(r"grok_web_fn_ = (\w+)", BLOK)
    assert atamalar.count("_ask_grok_web") == 1, f"beklenmeyen ham atama: {atamalar}"
    assert "_grok_web_tavanli" in atamalar
