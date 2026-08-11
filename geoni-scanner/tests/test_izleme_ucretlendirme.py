"""Izleme taramasi UCRETLIDIR ve bakiyesizken SIRAYI KAYBETMEZ.

Bu dosya iki ayri kusuru kilitliyor; ikisi de sessiz, ikisi de para/guven yakiyor.

1. `deduct=False`'a geri donus — PARA SIZINTISI
   Izleme taramalari 2026-08-12'ye kadar ucretsizdi. Bedava is gelir
   getirmedigi icin ozellik 2026-08-08'de TAMAMEN KAPATILDI ("11 hedef x
   15 gunde bir x ~$0,31 ≈ $7/ay, karsiliginda sifir gelir"). Kurucu karariyla
   (2026-08-12: "bize maliyet hep ayni") bedel eklendi ve ozellik acildi.
   Biri `deduct=True`'yu geri `False` yaparsa hicbir test patlamaz, hicbir
   hata log'u dusmez — sadece her 15 gunde bir sessizce para akar.
   Bu yuzden AST ile kilitliyoruz.

2. Bakiyesizken `last_auto_scan_at`'in ILERI ITILMESI — GUVEN KAYBI
   Eski kod bakiye yetmeyince taramayi atliyor AMA tarama zamanini
   guncelliyordu. Sonuc: jeton yukleyen kullanici 15 GUN DAHA bekliyordu.
   Kullanicinin gordugu sey "odedim, hala taranmiyor" — destek kaydi acar,
   biz de sebebini bulamayiz cunku kod "calisiyor" gorunur.
   Dogru davranis: zamana DOKUNMA. Hedef sirada kalir, bakiye gelir gelmez
   bir sonraki turda taranir.

🪤 Neden davranissal test: 1. madde sozdizimsel olarak gecerli bir degisiklik
   (True->False), lint yakalamaz. 2. madde ise ancak cagrilarin YAPILMADIGINI
   gozleyerek gorulur — "bir sey olmadi"yi test etmek gerekiyor.
"""
import ast
import asyncio
from pathlib import Path

import pytest

import monitor
from scan_costs import WEB_SCAN_COST, BRAND_SCAN_COST, SOCIAL_SCAN_COST

KAYNAK = (Path(__file__).resolve().parent.parent / "monitor.py").read_text(encoding="utf-8")
AGAC = ast.parse(KAYNAK)


# ── 1) deduct=True kilidi ────────────────────────────────────────────────────

def _deduct_degerleri() -> list:
    """monitor.py icindeki save_audit/save_brand_check cagrilarinin deduct degeri."""
    bulunan = []
    for dugum in ast.walk(AGAC):
        if not isinstance(dugum, ast.Call):
            continue
        ad = getattr(dugum.func, "id", None) or getattr(dugum.func, "attr", None)
        if ad not in ("save_audit", "save_brand_check"):
            continue
        for kw in dugum.keywords:
            if kw.arg == "deduct":
                bulunan.append((ad, ast.literal_eval(kw.value)))
    return bulunan


def test_izleme_taramalari_kontor_duser():
    degerler = _deduct_degerleri()
    assert degerler, "monitor.py'de save_audit/save_brand_check cagrisi bulunamadi"
    for ad, deger in degerler:
        assert deger is True, (
            f"{ad}(deduct={deger}) — izleme taramasi UCRETSIZ hale gelmis. "
            "2026-08-12 kurucu karari: izleme elle taramayla ayni bedeli oder."
        )


def test_her_iki_tarama_yolu_da_kapsandi():
    """Web ve marka yollarinin IKISI de duser — biri atlanirsa yarim sizinti olur."""
    adlar = {ad for ad, _ in _deduct_degerleri()}
    assert adlar == {"save_audit", "save_brand_check"}, (
        f"beklenen iki yol, bulunan: {adlar}"
    )


# ── 2) bedel tek kaynaktan ──────────────────────────────────────────────────

@pytest.mark.parametrize("tip,beklenen", [
    ("web", WEB_SCAN_COST),
    ("social", SOCIAL_SCAN_COST),
    ("person", BRAND_SCAN_COST),
    ("brand", BRAND_SCAN_COST),
    (None, BRAND_SCAN_COST),          # tip yoksa marka bedeli
])
def test_izleme_bedeli_tipe_gore(tip, beklenen):
    assert monitor._izleme_bedeli({"type": tip}) == beklenen


def test_bedel_scan_costs_ten_gelir_elle_yazilmaz():
    """Bedel sabitleri monitor.py'ye ELLE yazilmamali.

    scan_costs.py'nin bastaki notu: bu sayilar bir zamanlar alti ayri yerde
    elle yaziliydi ve biri degisip otekiler kalinca `credits_spent` ile
    gercekten dusulen tutar birbirini tutmuyordu.
    """
    govde = ast.get_source_segment(KAYNAK, next(
        d for d in ast.walk(AGAC)
        if isinstance(d, ast.FunctionDef) and d.name == "_izleme_bedeli"
    ))
    for sayi in ("20", "10"):
        assert sayi not in govde, (
            f"_izleme_bedeli govdesinde duz sayi '{sayi}' var — "
            "bedel scan_costs.py'den gelmeli"
        )


# ── 3) bakiyesizken sira kaybolmaz ──────────────────────────────────────────

class _Casus:
    """Cagrilip cagrilmadigini kaydeden sahte."""
    def __init__(self, donus=None):
        self.cagrildi = False
        self.cagrilar = []
        self._donus = donus

    async def __call__(self, *a, **k):
        self.cagrildi = True
        self.cagrilar.append((a, k))
        return self._donus


def _bakiye_ile_kos(monkeypatch, bakiye: int, tip: str = "web"):
    """_process_item'i verilen bakiyeyle kosturur; casuslari dondurur."""
    guncelle = _Casus()
    bildir = _Casus()
    slot_al = _Casus()
    tara = _Casus(donus=42)

    monkeypatch.setattr(monitor, "get_credit_balance", _Casus(donus=bakiye))
    monkeypatch.setattr(monitor, "update_watchlist_after_scan", guncelle)
    monkeypatch.setattr(monitor, "_duraklatma_bildir", bildir)
    monkeypatch.setattr(monitor, "acquire_scan_slot", slot_al)
    monkeypatch.setattr(monitor, "release_scan_slot", lambda *a, **k: None)
    monkeypatch.setattr(monitor, "_scan_web_item", tara)
    monkeypatch.setattr(monitor, "_scan_brand_item", tara)

    item = {"id": "x1", "user_id": "u1", "label": "ornek.com",
            "type": tip, "last_score": 40}
    asyncio.run(monitor._process_item(item))
    return {"guncelle": guncelle, "bildir": bildir, "tara": tara}


def test_bakiye_yetmezse_tarama_yapilmaz(monkeypatch):
    c = _bakiye_ile_kos(monkeypatch, bakiye=WEB_SCAN_COST - 1)
    assert not c["tara"].cagrildi, "bakiye yetmezken tarama kosmus — para yaniyor"


def test_bakiye_yetmezse_SIRA_ILERI_ITILMEZ(monkeypatch):
    """Asil kilit: zaman guncellenirse kullanici jeton yukleyince 15 gun bekler."""
    c = _bakiye_ile_kos(monkeypatch, bakiye=0)
    assert not c["guncelle"].cagrildi, (
        "bakiyesizken update_watchlist_after_scan cagrilmis — hedefin sirasi "
        "15 gun ileri itildi. Jeton yukleyen kullanici beklemek zorunda kalir."
    )


def test_bakiye_yetmezse_kullanici_bilgilendirilir(monkeypatch):
    c = _bakiye_ile_kos(monkeypatch, bakiye=0)
    assert c["bildir"].cagrildi, "duraklama bildirimi gonderilmemis — sessiz basarisizlik"


def test_bakiye_tam_yeterse_taranir(monkeypatch):
    """Sinir degeri: bakiye == bedel taranmali (bakiye > bedel degil)."""
    c = _bakiye_ile_kos(monkeypatch, bakiye=WEB_SCAN_COST)
    assert c["tara"].cagrildi, "bakiye tam bedele esitken tarama atlanmis"


# ── 4) target iki bicimde gelebiliyor ───────────────────────────────────────

@pytest.mark.parametrize("girdi,beklenen", [
    ({"name": "X", "topic": "Y"}, {"name": "X", "topic": "Y"}),   # sozluk aynen
    ("Filiz Alkan", {"name": "Filiz Alkan"}),                     # METIN -> ad
    ("  bosluklu  ", {"name": "bosluklu"}),
    (None, {}),
    ("", {}),
    ("   ", {}),
    ([1, 2], {}),                                                 # beklenmedik tip
    (42, {}),
])
def test_hedef_sozlugu_her_bicimi_kaldirir(girdi, beklenen):
    """🔴 CANLI OLAY (2026-08-12): `target` bazi satirlarda duz METIN.

    Izleme acilir acilmaz ODEYEN MUSTERININ iki hedefi ust uste coktu:
    'Filiz Alkan' ve 'Alkan Makina' -> "'str' object has no attribute 'get'".
    Skor None yazildi, kontor dusmedi, kullanici hicbir sey gormedi —
    tam anlamiyla SESSIZ basarisizlik. Izleme 2026-08-08'den beri kapali
    oldugu icin bu yol hic kosmamis, hata da hic gorunmemisti.

    Ders: veritabanindan gelen jsonb alanin TIPINE guvenme; ayni kolonda
    iki bicim yasayabiliyor.
    """
    assert monitor._hedef_sozlugu({"target": girdi}) == beklenen


def test_metin_hedef_COKMEZ(monkeypatch):
    """Metin target'li bir kayit _process_item'i patlatmamali."""
    c = _bakiye_ile_kos(monkeypatch, bakiye=BRAND_SCAN_COST, tip="person")
    assert c["tara"].cagrildi
    # asil kanit: asagidaki cagri istisna atmadan donuyor
    assert monitor._hedef_sozlugu({"target": "Alkan Makina"})["name"] == "Alkan Makina"


def test_sosyal_hedef_yarim_bedelle_taranir(monkeypatch):
    """Sosyal bedeli 10; 10 jetonu olan sosyal hedef taranabilmeli."""
    c = _bakiye_ile_kos(monkeypatch, bakiye=SOCIAL_SCAN_COST, tip="social")
    assert c["tara"].cagrildi, (
        "sosyal hedef web bedeliyle olculmus olabilir — _izleme_bedeli tipi "
        "dogru okumuyor"
    )
