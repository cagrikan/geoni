"""Ozel (private) tarama sonuclari icin kisa omurlu paylasimli depo.

NEDEN VAR (kor denetim 2026-08-04):
`request.private=True` ile acilan kisi/marka/sosyal taramalarin sonucu bilincli
olarak DB'ye YAZILMAZ — "hicbir yerde kaydedilmedi" sozu (main.py'deki private
dali). Sonuc yalnizca calistigi surecin bellegindeki `brand_checks_store`'da
duruyordu. Ama bu uclar SQS'e dusmez; her zaman istegi alan App Runner
instance'inda `background_tasks` ile kosar. Poll istegi (GET .../{job_id})
BASKA bir instance'a duserse:
    job_id bellekte yok  ->  DB'de de yok (private hic yazmiyor)  ->  404
Kullanici kredisini odemistir (dusum sonuctan ONCE, atomik) ama raporunu ASLA
goremez. App Runner MinSize=1/MaxSize=25 ile yapilandirilmis; bugun tek
instance'ta gizli, trafik artinca kacinilmaz.

COZUM: sonucu TTL'li olarak Upstash'e da yaz. DB'ye yazmadigimiz icin "hicbir
yere kaydedilmedi" sozu korunur — bu kalici bir kayit degil, suresi dolunca
kendiliginden silinen bir teslim kuyrugudur (bugun `sweep_private_results`
zaten 6 saatte temizliyor; buradaki TTL ondan KISA).

Redis yoksa/baglanamazsa modul sessizce devre disi kalir ve davranis eskisi
gibi (yalniz bellek) olur — hicbir sey kirilmaz.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

# Sonuc teslim penceresi. Kullanici taramayi baslatip sonucu bekliyor; 1 saat
# hem en uzun taramayi (olculen max 44 dk) hem makul bir okuma suresini karsilar.
TTL_SANIYE = 3600

_ONEK = "geoni:private-job:"

_REDIS_URL = os.environ.get("REDIS_URL", "")
_client = None
if _REDIS_URL and not _REDIS_URL.startswith("redis://localhost"):
    try:
        import redis  # noqa: PLC0415  (opsiyonel bagimlilik)
        _client = redis.from_url(_REDIS_URL, socket_timeout=3,
                                 socket_connect_timeout=3, decode_responses=True)
        _client.ping()
        logger.info("Ozel tarama deposu: paylasimli Redis aktif")
    except Exception as e:
        logger.warning(f"Ozel tarama deposu: Redis yok, yalniz bellek ({e})")
        _client = None


def aktif() -> bool:
    return _client is not None


def yaz(job_id: str, veri: dict) -> bool:
    """Sonucu TTL ile yaz. Basarisizlik SESSIZ: teslim yolu bellekte zaten var,
    burasi yalniz cok-instance yedegi. Hata taramayi dusurmemeli."""
    if _client is None or not job_id or not isinstance(veri, dict):
        return False
    try:
        _client.setex(_ONEK + job_id, TTL_SANIYE, json.dumps(veri, ensure_ascii=False))
        return True
    except Exception as e:
        logger.warning(f"ozel tarama yazilamadi ({job_id}): {e}")
        return False


def oku(job_id: str) -> dict | None:
    """Bellekte bulunamayan ozel taramayi paylasimli depodan getir."""
    if _client is None or not job_id:
        return None
    try:
        ham = _client.get(_ONEK + job_id)
        return json.loads(ham) if ham else None
    except Exception as e:
        logger.warning(f"ozel tarama okunamadi ({job_id}): {e}")
        return None


def sil(job_id: str) -> None:
    """Teslim edildi -> hemen sil (ozel tarama tek kullanimlik teslim edilir)."""
    if _client is None or not job_id:
        return
    try:
        _client.delete(_ONEK + job_id)
    except Exception:
        pass
