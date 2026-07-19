"""A4-5 (QA 2026-07-19): Sentry hata toplama — GUVENLI no-op.

SENTRY_DSN yoksa YA DA sentry_sdk paketi henuz kurulu degilse (deploy gecisi)
HICBIR SEY yapmaz, import/init cokmez. DSN eklenince API + worker unhandled
exception'lari uretimde ILK olustugunda gorunur — QA turunu beklemez (QA'da
50k->500 ancak elle canli test edilince gorulmustu). Tek geliştirici icin
"sessiz hata" sinifinin en ucuz panzehiri.
"""
import logging
import os

logger = logging.getLogger(__name__)


def init_sentry(component: str) -> None:
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return
    try:
        import sentry_sdk  # paket yoksa ImportError -> sessiz gec
        sentry_sdk.init(
            dsn=dsn,
            environment=os.environ.get("GEONI_ENV", "production"),
            traces_sample_rate=0.0,      # sadece hata; performans izleme kapali (maliyet)
            send_default_pii=False,      # kullanici verisi gonderme
            server_name=f"geoni-{component}",
        )
        logger.info(f"Sentry aktif ({component})")
    except Exception as e:
        logger.warning(f"Sentry init atlandi ({component}): {e}")
