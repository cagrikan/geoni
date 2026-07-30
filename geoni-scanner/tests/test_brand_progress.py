"""Marka/kisi/sosyal bekleme ekraninin GERCEK ilerlemesi (2026-07-30).

Mobil bekleme ekrani ilerlemeyi `min(0.9, elapsed/120)` ile UYDURUYORDU: 108.
saniyede %90'a cikip donuyor, tarama daha uzun surunce orada kilitleniyordu.
brand_recall zaten adim yayinliyordu (SSE) ama poll eden mobil bunu goremiyordu.
Bu testler iki kirilma noktasini korur:

1. main.py'nin faz listesi ile brand_recall'un adim anahtarlari birbirinden
   KAYMASIN (biri yeniden adlandirilirsa ilerleme sessizce olur).
2. Ilerleme MONOTONIK kalsin (kullanici "tarama bastan basladi" sanmasin).
"""
import brand_recall
# DIKKAT: `main` import EDILMEZ. Deploy kapisi testleri minimal ortamda kosuyor
# (pytest/httpx/cbor2/cryptography); main.py fastapi cektigi icin orada
# import edilemez ve tum suite collection'da patlar. Saf mantik brand_progress'te.
from brand_progress import (BRAND_MODEL_COUNT, BRAND_PROGRESS_STEPS,
                            BRAND_STEP_ALIAS, brand_ilerleme_guncelle,
                            yeni_brand_ilerlemesi)


def test_faz_anahtarlari_brand_recall_ile_ayni():
    """Faz + alias adlarinin HEPSI brand_recall'un yayin anahtari olmali."""
    yayin = set(brand_recall.PROGRESS_MESSAGES["tr"])
    bilinen = set(BRAND_PROGRESS_STEPS) | set(BRAND_STEP_ALIAS)
    assert bilinen <= yayin, f"main.py bilinmeyen adim biliyor: {bilinen - yayin}"


def test_her_yayin_anahtari_ele_alinmis():
    """brand_recall yeni bir adim yayinlarsa main.py sessizce yok saymasin."""
    yayin = set(brand_recall.PROGRESS_MESSAGES["tr"])
    ele_alinan = set(BRAND_PROGRESS_STEPS) | set(BRAND_STEP_ALIAS) | {
        "model_answered", "model_no_answer"}
    assert yayin <= ele_alinan, f"main.py bu adimlari yok sayiyor: {yayin - ele_alinan}"


def test_tr_en_ayni_anahtarlar():
    assert set(brand_recall.PROGRESS_MESSAGES["tr"]) == set(brand_recall.PROGRESS_MESSAGES["en"])


def test_normal_akis_ilerler():
    p = yeni_brand_ilerlemesi()
    for adim in ["web_search", "verifying_identity", "sov", "querying_models",
                 "comparing", "scoring"]:
        brand_ilerleme_guncelle(p, adim)
    assert p["step"] == "scoring"
    assert p["index"] == len(BRAND_PROGRESS_STEPS) - 1


def test_ilk_adim_index_sifirda_da_kaydedilir():
    """index varsayilani 0; ilk adim da 0 -> 'i > index' tek basina yetmez."""
    p = yeni_brand_ilerlemesi()
    brand_ilerleme_guncelle(p, "web_search")
    assert p["step"] == "web_search" and p["index"] == 0


def test_kimlik_dogrulama_atlanabilir():
    """verifying_identity kosula bagli (web sonucu + guclu baglam + OPENAI)."""
    p = yeni_brand_ilerlemesi()
    brand_ilerleme_guncelle(p, "web_search")
    brand_ilerleme_guncelle(p, "querying_models")
    assert p["step"] == "querying_models"


def test_sov_faz_degistirmez_ama_geri_de_almaz():
    """sov, querying_models'e alias'lidir; sonrasinda gelirse geri sarmamali."""
    p = yeni_brand_ilerlemesi()
    for adim in ["web_search", "querying_models", "comparing"]:
        brand_ilerleme_guncelle(p, adim)
    brand_ilerleme_guncelle(p, "sov")
    assert p["step"] == "comparing"


def test_monotonik_geri_gitmez():
    p = yeni_brand_ilerlemesi()
    brand_ilerleme_guncelle(p, "scoring")
    brand_ilerleme_guncelle(p, "web_search")
    assert p["step"] == "scoring"


def test_motor_sayaci_faz_degistirmez_ve_tavanli():
    p = yeni_brand_ilerlemesi()
    brand_ilerleme_guncelle(p, "querying_models")
    for _ in range(BRAND_MODEL_COUNT + 3):
        brand_ilerleme_guncelle(p, "model_answered")
    assert p["models_done"] == BRAND_MODEL_COUNT
    assert p["step"] == "querying_models"
    assert p["models_total"] == BRAND_MODEL_COUNT


def test_yanit_vermeyen_motor_da_sayilir():
    """Motor cokerse sayac ilerlemezse ilerleme cubugu takilirdi."""
    p = yeni_brand_ilerlemesi()
    brand_ilerleme_guncelle(p, "model_no_answer")
    assert p["models_done"] == 1


def test_bilinmeyen_adim_yok_sayilir():
    p = yeni_brand_ilerlemesi()
    brand_ilerleme_guncelle(p, "web_search")
    brand_ilerleme_guncelle(p, "olmayan_adim")
    assert p["step"] == "web_search"


def test_emit_anahtardan_yerellestirilmis_metin_ve_adim_uretir():
    """emit() ARTIK metin degil anahtar aliyor: iki geri cagirma tek kaynaktan
    beslenir, yani SSE metni ile makine-okunur adim birbirinden kayamaz."""
    metinler, adimlar = [], []
    msgs = brand_recall.PROGRESS_MESSAGES["en"]

    def emit(key, **fmt):
        message = msgs.get(key) or msgs["scoring"]
        if fmt:
            message = message.format(**fmt)
        metinler.append(message)
        adimlar.append(key)

    emit("web_search")
    emit("model_answered", label="Claude")

    assert adimlar == ["web_search", "model_answered"]
    assert metinler[0] == msgs["web_search"]
    assert "Claude" in metinler[1]
