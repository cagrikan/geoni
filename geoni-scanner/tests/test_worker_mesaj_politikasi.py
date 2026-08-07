"""Worker'ın mesaj silme / DLQ politikası — para ve veri kaybı burada.

`worker.process_message` her SQS mesajı için tek bir karar veriyor:
**mesaj silinsin mi, yoksa kuyrukta bırakılıp DLQ'ya mı düşsün.**

Yanlış karar iki yönde de pahalı:
  * gereksiz `delete_after=False` → mesaj tekrar tekrar teslim edilir, her
    teslimde yeni bir tarama koşar ve **her tarama gerçek API parası yakar**;
  * gereksiz `delete_after=True` → çöken bir işlemin mesajı silinir, tarama
    sessizce kaybolur ve kullanıcı sonucunu hiç alamaz.

🪤 CI ORTAMI: `worker.py` modül seviyesinde `main`'i import ediyor, `main` de
fastapi'yi. Deploy kapısı fastapi kurmuyor → import eden test CI'da toplama
aşamasında düşer (bu gece `test_card.py` ile aynen yaşandı). Bu yüzden testler
worker.py'yi **DOSYA olarak** okuyup politikayı kilitliyor; her ortamda koşar.
Kırılganlığın farkındayım: bu bir davranış testi değil, politika kapanı —
amacı, ileride biri bu dalları sessizce değiştirdiğinde gürültü çıkarmak.
"""
import re
from pathlib import Path

KAYNAK = (Path(__file__).resolve().parent.parent / "worker.py").read_text(encoding="utf-8")
GOVDE = KAYNAK[KAYNAK.index("async def process_message"):KAYNAK.index("async def run()")]


def _dal(baslangic: str, uzunluk: int = 420) -> str:
    i = GOVDE.find(baslangic)
    assert i >= 0, f"dal bulunamadı: {baslangic}"
    return GOVDE[i:i + uzunluk]


def test_prewarm_mesaji_SILINIR():
    """Isıtma mesajı iş taşımaz; silinmezse kuyrukta birikir ve worker'ı
    boşuna ayakta tutar."""
    dal = _dal('if kind == "prewarm"')
    assert "delete_after = False" not in dal, "prewarm dalı mesajı DLQ'ya bırakıyor"
    assert "return" in dal


def test_bilinmeyen_is_turu_DLQ_ya_birakilir():
    """Tanımadığımız bir iş türünü sessizce silmek veriyi kaybetmektir."""
    dal = _dal('if kind != "web_audit"')
    assert "delete_after = False" in dal


def test_beklenmedik_hata_DLQ_ya_birakilir():
    """Parse/beklenmedik hatada mesaj kuyrukta kalmalı → redrive → DLQ."""
    dal = _dal("except Exception as e:")
    assert "delete_after = False" in dal


def test_basarisiz_tarama_mesaji_SILINIR():
    """🪤 Bilinçli karar: uygulama-içi hata DETERMINISTIKTIR, yeniden denemek
    aynı hatayı üretir ve YALNIZCA para yakar. Bu yüzden `failed` sonucunda da
    mesaj silinir. Yorum kaynakta duruyor; kaybolursa biri 'hata varsa retry
    edelim' diye değiştirir ve maliyet katlanır."""
    assert "failed dahil siliyoruz" in GOVDE
    assert "retry" in GOVDE and "maliyet" in GOVDE


def test_koruma_mesaj_SILINDIKTEN_SONRA_birakilir():
    """🪤 Yarış: koruma önce bırakılsaydı, silme ile korumanın kalkması
    arasındaki pencerede ECS görevi öldürülüp mesaj TEKRAR teslim edilebilirdi
    — kapatmaya çalıştığımız yarışın aynısı."""
    i_sil = GOVDE.find("delete_message")
    i_release = GOVDE.find("task_protection.release")
    assert i_sil > 0 and i_release > 0
    assert i_release > i_sil, "koruma, mesaj silinmeden ÖNCE bırakılıyor"


def test_koruma_ISLEMEDEN_once_alinir():
    """Erken `return` eden dallar (prewarm / bilinmeyen tür) finally'ye düşüyor;
    koruma orada bırakılıyor. Alma işlemeden önce olmazsa sayaç eksiye iner."""
    i_acquire = GOVDE.find("task_protection.acquire")
    i_parse = GOVDE.find("json.loads")
    assert 0 < i_acquire < i_parse


def test_heartbeat_her_durumda_durdurulur():
    """Durdurulmazsa görev bitse de heartbeat SQS görünürlüğünü uzatmaya devam
    eder; mesaj kilitli kalır."""
    i_finally = GOVDE.find("finally:")
    assert i_finally > 0
    kuyruk = GOVDE[i_finally:]
    assert "stop.set()" in kuyruk and "await hb" in kuyruk


def test_bellek_ici_sozlukler_temizlenir():
    """`jobs_store` ve `audit_events` temizlenmezse uzun ömürlü worker'da
    bellek sızıntısı olur (her tarama bir giriş bırakır)."""
    i_finally = GOVDE.find("finally:")
    kuyruk = GOVDE[i_finally:]
    assert "jobs_store.pop" in kuyruk
    assert "audit_events.pop" in kuyruk
