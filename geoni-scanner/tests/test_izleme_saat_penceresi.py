"""Izleme dongusu yalniz TR 09:00-21:00 arasinda tarar (kurucu istegi 2026-09-05).

Gece 03:19'da giden "izleme beklemede" e-postasi bu kuralin sebebi."""
import asyncio
from datetime import datetime, timezone

import monitor


def _utc(h, m=0):
    return datetime(2026, 9, 5, h, m, tzinfo=timezone.utc)


def test_gece_uc_kapali():
    assert monitor.izleme_saati_uygun(_utc(0, 19)) is False   # TR 03:19


def test_sabah_dokuz_acik_sekiz_ellidokuz_kapali():
    assert monitor.izleme_saati_uygun(_utc(6, 0)) is True      # TR 09:00
    assert monitor.izleme_saati_uygun(_utc(5, 59)) is False    # TR 08:59


def test_aksam_yirmi_ellidokuz_acik_yirmibir_kapali():
    assert monitor.izleme_saati_uygun(_utc(17, 59)) is True    # TR 20:59
    assert monitor.izleme_saati_uygun(_utc(18, 0)) is False    # TR 21:00


def test_saatsiz_datetime_utc_sayilir():
    assert monitor.izleme_saati_uygun(datetime(2026, 9, 5, 0, 19)) is False


def test_dongu_gece_kuyrugu_sorgulamaz(monkeypatch):
    """Gece: list_due_watchlist_items HIC cagrilmaz."""
    cagri = []
    monkeypatch.setattr(monitor, "OTOMATIK_TARAMA_ACIK", True)
    monkeypatch.setattr(monitor, "izleme_saati_uygun", lambda *a, **k: False)

    async def sahte_liste(**k):
        cagri.append(k); return []
    monkeypatch.setattr(monitor, "list_due_watchlist_items", sahte_liste)

    async def bos(*a, **k):
        return []
    monkeypatch.setattr(monitor, "run_low_balance_alert", bos)
    monkeypatch.setattr(monitor, "_claim_daily_job", bos)

    class Dur(Exception):
        pass

    async def uyku(saniye):
        if saniye != 120:
            raise Dur()
    monkeypatch.setattr(monitor.asyncio, "sleep", uyku)
    try:
        asyncio.run(monitor.monitor_loop())
    except Dur:
        pass
    assert cagri == []
