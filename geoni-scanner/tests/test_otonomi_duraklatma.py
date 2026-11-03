"""Üretim otonomi kapısı kullanıcı taramalarını kapatmadan arka plan işlerini durdurur."""
from pathlib import Path


KAYNAK = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")


def test_otonomi_varsayilan_acik_ama_uretimden_duraklatilabilir():
    assert 'os.environ.get("GEONI_AUTONOMY_PAUSED", "0")' in KAYNAK
    assert '.strip() == "1"' in KAYNAK


def test_icerik_ve_oz_gelisim_kapinin_arkasinda():
    i = KAYNAK.index("if _autonomy_paused:")
    govde = KAYNAK[i:i + 1200]
    assert govde.index("asyncio.create_task(content_loop())") > govde.index("else:")
    assert govde.index("asyncio.create_task(improvement_loop())") > govde.index("else:")


def test_bakim_dongusu_otonomi_kapisinin_disinda():
    i = KAYNAK.index("if _autonomy_paused:")
    assert KAYNAK.index("asyncio.create_task(monitor_loop())") < i
