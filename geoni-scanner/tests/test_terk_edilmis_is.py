"""Marka/sosyal isi surec olurse "Tarama bulunamadi." demesin.

CANLIDA YASANDI (2026-08-08, kurucunun testcisi mobilde): sosyal tarama
gonderildi, "olculuyor" ekranina gecti, ~2 dk sonra ekranda

    "Tarama bulunamadi."

Kok neden: marka/kisi/sosyal isleri `background_tasks` ile SUREC ICINDE kosuyor
ve durum yalniz bellekteki `brand_checks_store`'da tutuluyordu. Surec yeniden
baslayinca (dagitim, App Runner MinCapacity=0'dan uyanma, olcekleme) is
bellekten siliniyor; DB'de de satir YOK, cunku `save_brand_check` satiri isin
SONUNDA yaziyor. `/api/brand-check/{id}` -> 404.

Web taramasi bunu HIC yasamiyordu: `create_pending_audit` submit'te satiri
aciyordu. Asimetri buradaydi — gunun ucuncu "web'de var, sosyalde yok" kusuru.

Iki tarafi da kapatiyoruz:
  1. Submit'te 'queued' satiri acilir (create_pending_brand_check).
  2. Satir bulunur ama COK ESKIYSE (>20 dk) durum "kosuyor" degil BASARISIZ
     doner — kimse o satiri guncellemeyecek, kullaniciyi sonsuz bekleme
     ekraninda tutmak 404'ten de kotudur.

🪤 Test `main`i IMPORT ETMEZ (fastapi zinciri; CI'in asgari ortami toplama
   asamasinda duserdi). Kaynak okunarak kilitlenir.
"""
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
MAIN = (KOK / "main.py").read_text(encoding="utf-8")
DB = (KOK / "db.py").read_text(encoding="utf-8")


def test_IKI_UCTA_DA_queued_satiri_aciliyor():
    """Marka ve sosyal — ikisi de. Birini yapip digerini atlamak bugunku hata bicimi."""
    assert MAIN.count("await create_pending_brand_check(") == 2, \
        "iki uctan birinde submit'te satir acilmiyor"


def test_satir_isin_BASINDA_aciliyor():
    """Kuyruga girmeden once olmali; sonra acmak dayanikliligi saglamaz."""
    for imza in ('request.type or "person"', '"social"'):
        i = MAIN.index(f"create_pending_brand_check(job_id, {imza}")
        j = MAIN.index("brand_check_events[job_id] = asyncio.Queue()", max(0, i - 1500))
        assert i < j, f"{imza}: satir kuyruk kurulduktan SONRA aciliyor"


def test_tamamlanma_yazisi_UPSERT():
    """🔴 Yoksa ayni id ile duz POST 409 doner: sonuc kaydedilmez, kontor dusulmez."""
    i = DB.index("async def save_brand_check")
    govde = DB[i:i + 3000]
    assert "resolution=merge-duplicates" in govde


def test_terk_edilmis_is_BASARISIZ_doner():
    """Sonsuz "kosuyor" ekrani 404'ten kotudur: istemci yeniden bile denemez."""
    i = MAIN.index("async def get_brand_check_status")
    govde = MAIN[i:i + 2500]
    assert "_satir_yasi_sn(" in govde
    assert "1200" in govde, "esik yok"
    assert govde.index("_satir_yasi_sn(") < govde.index('return {"job_id": job_id, "status": row["status"]'), \
        "yas kontrolu 'kosuyor' donusunden SONRA — hic calismaz"


def test_yas_cozulemezse_ESKI_SAYILMAZ():
    """🪤 Guvenli taraf: damga okunamiyorsa calisan isi OLDURME."""
    i = MAIN.index("def _satir_yasi_sn")
    govde = MAIN[i:i + 900]
    assert "return None" in govde
    assert "yas is not None and yas >" in MAIN, "None esikle karsilastirilirsa TypeError"


def test_Z_ve_tzsiz_damga_ELE_ALINIYOR():
    """🪤 Python 3.10 fromisoformat 'Z'yi anlamiyor; tzsiz damga cikarmada patlar.
    Ikisi de sessizce None uretirdi -> kontrol hic calismazdi."""
    i = MAIN.index("def _satir_yasi_sn")
    govde = MAIN[i:i + 900]
    assert 'replace("Z", "+00:00")' in govde
    assert "tzinfo is None" in govde


def test_pending_satiri_TARAMAYI_OLDURMEZ():
    """DB'nin gecici hatasi kullanicinin taramasini dusurmemeli."""
    i = DB.index("async def create_pending_brand_check")
    govde = DB[i:i + 2200]
    assert "return False" in govde and "raise" not in govde.split("try:")[1][:600]


# ── Cache isabeti: acilan satir GERI ALINIR ──────────────────────────────
# 🔴 OLCULDU (2026-08-08): `create_pending_brand_check` eklendikten sonra ayni
# hedef 24 saat icinde yeniden tarandiginda satir sonsuza dek 'queued' kaliyordu.
# Sebep: 24 saatlik tarama cache'i isabet edince `run_brand_check_job` sonucu
# bellekten donup RETURN ediyor -- `save_brand_check` HIC cagrilmiyor (bu dogru:
# yeni olcum yok, kullanici ikinci kez ucretlendirilmemeli).
#
# Sonuc iki katli kotuydu:
#   1. Kullanicinin gecmisinde sonsuza dek "kuyrukta" duran sahte kayit.
#   2. 20 dk sonra yukaridaki eskime kurali onu TERK EDILMIS sayip BASARISIZ
#      donduruyordu -- elde gecerli bir cached sonuc VARKEN. Yani dayaniklilik
#      icin eklenen satir, cache isabetinde tam tersi etkiyi yapiyordu.
#
# Bes tekrarda bes kez uretildi (@nike/spor, ayni sov skoru 26.2 -> cache isabeti
# oldugunun kaniti).
def test_cache_isabetinde_pending_satiri_SILINIYOR():
    i = MAIN.index('"cached": True')
    blok = MAIN[i:i + 1200]
    assert "await pending_satiri_sil(job_id)" in blok, \
        "cache dalinda acilan 'queued' satiri geri alinmiyor"
    assert blok.index("pending_satiri_sil") < blok.index("return"), \
        "silme `return`dan SONRA yaziliyor — hic calismaz"


def test_YALNIZ_queued_satir_siliniyor():
    """🪤 Tamamlanmis satiri silmek gercek raporu ve kontor kaydini yok ederdi."""
    i = DB.index("async def pending_satiri_sil")
    govde = DB[i:i + 1800]
    assert '"status": "eq.queued"' in govde, "status kosulu yok — her satiri siler"
    assert "client.delete(" in govde


def test_silme_HATASI_taramayi_dusurmez():
    i = DB.index("async def pending_satiri_sil")
    govde = DB[i:i + 1800]
    assert "return False" in govde
