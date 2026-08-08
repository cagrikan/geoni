"""Grok KAPALIYKEN tarama akışı kırılmaz — kurucu kararı 2026-08-08 sonrası kapan.

Grok kapatıldı: `XAI_API_KEY` sırrı hem ECS `geoni-scan-worker` rev 8'den hem
App Runner'dan kaldırıldı, `GROK_WEB_SHADOW` env'i silindi. Yani üretimde
`_ask_grok` ve `_ask_grok_web` artık **hiç çağrılmıyor**.

Bu, canlıda çalışan bir motoru koşu anında yok etmek demek. Üç yerde sessizce
patlayabilirdi ve üçü de kaynak okunarak doğrulandı (2026-08-08); bu testler o
korumaları KİLİTLER:

1. `_ask_grok` anahtar yoksa `None` döner (istisna fırlatmaz).
2. `_check_model_two_phase` `None` yanıtı "ölçülemedi" sayar
   (`got_any_response` False → `measured=False`), çökmez.
3. SOV tarafı `ask_grok_web=None` ile çağrılabilir ve gövde bunu `is not None`
   ile korur — aksi halde her taramada `TypeError`.

🪤 Bu testler `brand_recall`/`sov`'u IMPORT ETMEZ: ikisi de httpx/anthropic
zincirini çekiyor, CI'ın asgari ortamı (`pytest httpx cbor2 cryptography`)
toplama aşamasında düşerdi — bu hafta iki kez yaşandı.
"""
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
BR = (KOK / "brand_recall.py").read_text(encoding="utf-8")
SOV = (KOK / "sov.py").read_text(encoding="utf-8")


def test_anahtar_yoksa_None_doner_ISTISNA_DEGIL():
    i = BR.index("async def _ask_grok(")
    govde = BR[i:i + 900]
    assert "if not GROK_API_KEY:" in govde
    j = govde.index("if not GROK_API_KEY:")
    assert "return None" in govde[j:j + 60], "anahtar yokken istisna fırlatılıyor"


def test_bos_yanit_OLCULEMEDI_sayiliyor():
    """🪤 Asıl kapan: `not raw` dalı silinirse None yanıt parse'a girer ve çöker."""
    i = BR.index("async def _check_model_two_phase")
    govde = BR[i:i + 1600]
    assert "if isinstance(raw, Exception) or not raw:" in govde
    assert "got_any_response = False" in govde
    assert 'd.setdefault("measured", True)' in BR, "measured sözleşmesi kaybolmuş"


def test_sov_grok_web_None_KABUL_EDIYOR():
    """🪤 Varsayılan None olmalı ve gövde korumalı olmalı; yoksa TypeError."""
    assert "ask_grok_web=None" in SOV, "varsayılan None değil"
    assert "if ask_grok_web is not None:" in SOV, "None koruması yok"


def test_grok_agirligi_SIFIR_kalmali():
    """Gölge motor: kaybolması manşet skoru DEĞİŞTİRMEMELİ."""
    assert "WEIGHTS['grok']=0" in BR or 'WEIGHTS["grok"]=0' in BR or "WEIGHTS['grok'] = 0" in BR


def test_gerekce_kaynakta_YAZILI():
    """Birisi 'grok hiç çağrılmıyor, bu dallar ölü' deyip silmesin."""
    assert "Anahtar yoksa" in BR
