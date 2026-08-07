"""Düşük bakiye uyarısı: ayrışmayan sağlayıcı İZLENMEZ.

NE OLDU (2026-08-07'de ölçüldü): kurucuya HER GÜN "sağlayıcı bakiyesi $5 altına
düştü" maili gidiyordu. Sebep Anthropic'ti ve bakiye gerçekten düşük değildi:

`organizations/cost_report` **tüm organizasyonun** maliyetini döndürüyor ve
anahtar bazında ayrışmıyor — `group_by[]=workspace_id` hepsinde `null`,
`group_by[]=api_key_id` 400 veriyor. Yani GEONI'nin tarama harcaması, aynı
organizasyondaki diğer kullanımla toplanıyor. 5 Ağustos'ta org maliyeti
**$223,80** iken GEONI o gün neredeyse hiç tarama yapmadı.

`kalan = topup − harcama` olduğu için Anthropic her zaman eksiye düşüyor ve
uyarı her gün ateşliyor. Üretemediğimiz bir sayıyı izliyormuş gibi yapmak,
gerçek uyarıları da gürültüye boğar.
"""
import re
from pathlib import Path

KAYNAK = (Path(__file__).resolve().parent.parent / "db.py").read_text(encoding="utf-8")
BLOK = KAYNAK[KAYNAK.index("async def get_provider_remaining_balances"):]
BLOK = BLOK[:BLOK.index("async def", 60)]


def test_anthropic_bakiye_izlemesine_GIRMEZ():
    """🪤 Geri eklenirse günlük yanlış alarm da geri gelir."""
    i = BLOK.index("providers = {")
    sozluk = BLOK[i:BLOK.index("}", i)]
    assert '"anthropic"' not in sozluk


def test_ayrisabilen_saglayicilar_IZLENIYOR():
    """openai/gemini/perplexity/grok anahtarları GEONI'ye özel — onlar kalmalı."""
    i = BLOK.index("providers = {")
    sozluk = BLOK[i:BLOK.index("}", i)]
    for p in ("openai", "gemini", "perplexity", "grok"):
        assert f'"{p}"' in sozluk, f"{p} izlemeden düşmüş"


def test_gerekce_kaynakta_YAZILI():
    """Gerekçe silinirse biri 'anthropic neden yok' deyip geri ekler."""
    assert "TUM ORGANIZASYONUN" in BLOK
    assert "223" in BLOK


def test_harcamasi_hesaplanamayan_saglayici_ATLANIR():
    """Genel koruma: spend None ise yanlış alarm verilmez."""
    assert "spend is None" in BLOK
    assert "yanlis alarm verme" in BLOK
