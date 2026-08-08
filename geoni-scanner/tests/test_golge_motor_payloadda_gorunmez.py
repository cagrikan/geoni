"""Ölçülmeyen GÖLGE motor müşteri raporunda görünmez (2026-08-08).

Kurucu Grok'u kapattı: `XAI_API_KEY` hem ECS `geoni-scan-worker` rev 8'den hem
App Runner'dan silindi. Sonuç: `grok` artık HER taramada `measured=False`.
Filtre olmasaydı müşteri iki ayrı yerde yanlış bilgi görecekti:

1. `engines_unavailable` → her raporda **"Grok'a erişilemedi"** uyarısı.
   Bu alan CANLI bir skorlama motorunun eksik kalmasını haber vermek için var
   (kurucu kararı 2026-07-26: openai kredisi bitmiş, müşteri eksik ölçülmüş
   skoru tam sanmıştı). Ağırlığı 0 olan, hiç sormadığımız bir motor için arıza
   bildirmek yanlış alarmdır.
2. `model_results.grok` → `recognized=false, score=0`. İstemciler gölge motoru
   *"deneysel · skora katılmıyor"* etiketiyle ÇİZİYOR
   (`BrandCheckResultsPage.jsx`, mobil `app/brand/[jobId].tsx`), yani kullanıcı
   **"Grok beni tanımıyor"** okuyordu. Oysa Grok'a hiç sormadık.

🪤 Filtre YALNIZ gölge motoru ve YALNIZ ölçülmemişse düşürür. Canlı bir motor
düşerse müşteri uyarısı aynen çıkmaya devam etmeli — o koruma bilerek konuldu.

🪤 Test `brand_recall`'ı IMPORT ETMEZ (httpx/anthropic zinciri; CI'ın asgari
ortamı toplama aşamasında düşerdi). Kaynak okunarak kilitlenir.
"""
from pathlib import Path

KAYNAK = (Path(__file__).resolve().parent.parent / "brand_recall.py").read_text(encoding="utf-8")


def test_engines_unavailable_GOLGEYI_DISLIYOR():
    i = KAYNAK.index('"engines_unavailable": [')
    blok = KAYNAK[i:i + 320]
    assert "k not in SHADOW_ENGINES" in blok, "gölge motor müşteri uyarısına giriyor"


def test_engines_unavailable_CANLI_motoru_HALA_bildiriyor():
    """🪤 Asıl risk: filtre fazla geniş yazılıp canlı motor uyarısını da yutmak."""
    i = KAYNAK.index('"engines_unavailable": [')
    blok = KAYNAK[i:i + 320]
    assert 'not model_raw[k].get("measured", True)' in blok


def test_model_results_olculmemis_golgeyi_DUSURUYOR():
    # 🪤 index() erken donen `"model_results": {}` (bos sozluk, hata dallari)
    # satirini bulur; asil payload EN SONDAKI.
    i = KAYNAK.rindex('"model_results": {')
    blok = KAYNAK[i:i + 260]
    assert "k not in SHADOW_ENGINES" in blok
    assert 'model_raw.get(k, {}).get("measured", True)' in blok, \
        "ölçülmüş gölge motor da düşürülüyor — yanlış"


def test_canli_motorlar_HER_ZAMAN_kaliyor():
    """Canlı motor ölçülemese bile raporda kalmalı: skor kırılımı ona dayanıyor."""
    # 🪤 index() erken donen `"model_results": {}` (bos sozluk, hata dallari)
    # satirini bulur; asil payload EN SONDAKI.
    i = KAYNAK.rindex('"model_results": {')
    blok = KAYNAK[i:i + 260]
    # kosul "golge DEGILSE tut" ile basliyor -> canli motor her durumda kalir
    assert blok.index("k not in SHADOW_ENGINES") < blok.index("measured")


def test_gerekce_kaynakta_YAZILI():
    assert "GOLGE MOTOR BU LISTEYE GIRMEZ" in KAYNAK
    assert "hic SORMADIK" in KAYNAK
