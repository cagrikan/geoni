"""check_brand_recall POZİSYONEL cagrilmamali (canli kusur 2026-08-04).

NE OLDU: imzaya `need_topics` ve `need_sov` IKINCI ve UCUNCU parametre olarak
eklendi:

    async def check_brand_recall(name, need_topics=True, need_sov=True, topic="", ...)

ama cagri yerleri `topic`'i hala ikinci POZISYONEL argüman olarak geciyordu:

    check_brand_recall(identity["name"], identity["topic"], ..., need_topics=False)
                                         └─ need_topics'e gidiyor ─┘  └─ CAKISMA ─┘

Iki farkli ariza cikardi:
  · main.py (web taramasi): `need_topics` hem pozisyonel hem keyword ->
    TypeError -> HER WEB TARAMASI PATLADI. Kurucu TestFlight'ta canli yasadi.
  · monitor.py (izleme): keyword verilmedigi icin cakisma YOK ama `topic`
    parametresi bos kaldi -> SOV alan bilgisi sessizce kayboldu.

Testler yakalayamadi cunku bu cagri yollari birim testlerde mock'lu.

KURAL: `check_brand_recall` yalnizca `name` pozisyonel alir; gerisi keyword.
Imzaya yeni parametre eklendiginde cagri yerleri kaymasin diye.
"""
import inspect
import re
from pathlib import Path

import brand_recall

_KOK = Path(__file__).resolve().parent.parent


def test_imzada_topic_ikinci_parametre_degil():
    """Bu testin varlik sebebi: birisi 'topic ikinci parametre' varsayimiyla
    pozisyonel cagri yazarsa burada patlasin."""
    par = list(inspect.signature(brand_recall.check_brand_recall).parameters)
    assert par[0] == "name"
    assert par[1] != "topic", (
        "topic artik ikinci parametre degil — pozisyonel cagri sessizce yanlis "
        "parametreye gider")


def test_cagri_yerleri_yalniz_name_pozisyonel_gecer():
    for dosya in ("main.py", "monitor.py", "self_improve.py"):
        yol = _KOK / dosya
        if not yol.exists():
            continue
        kaynak = yol.read_text(encoding="utf-8")
        # cagriyi ve sonraki ~200 karakteri al
        for m in re.finditer(r"check_brand_recall\(\s*", kaynak):
            kuyruk = kaynak[m.end():m.end() + 220]
            # ilk argumandan sonraki ilk virgulden sonra '=' gelmeli (keyword)
            parca = kuyruk.split(",", 1)
            if len(parca) < 2:
                continue
            ikinci = parca[1].strip()
            # yorum satirlarini atla
            ikinci = "\n".join(l for l in ikinci.splitlines()
                               if not l.strip().startswith("#")).strip()
            if not ikinci or ikinci.startswith(")"):
                continue
            ad = ikinci.split("=")[0].strip()
            assert re.match(r"^[a-z_][a-z0-9_]*$", ad) and "=" in ikinci.split("\n")[0], (
                f"{dosya}: check_brand_recall ikinci argumani POZISYONEL "
                f"('{ikinci[:40]}...') — keyword olmali")
