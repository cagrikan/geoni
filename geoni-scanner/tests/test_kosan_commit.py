"""/health kosan commit'i soylemeli — "RUNNING" dagitim kaniti DEGILDIR.

2026-08-08'de bir izleyici yanlis "hala bozuk" raporu yazdi: App Runner
`Service.Status == RUNNING` diye bakiyordu, o da yalnizca "bir surum ayakta"
demek. Ittigim commit'in gercekten canliya cikip cikmadigini soylemiyordu.

Artik zincir su: buildspec derleme aninda `SURUM.txt`e SHA yazar (imajin icine
girer, COPY . . ile), `/health` onu geri verir. Dogrulama tek satir:

    curl -s https://api.geoni.ai/health | grep -q "$(git rev-parse HEAD)"

🪤 Dosya yoksa "bilinmiyor" doner — bos dize DEGIL. Bos dize, karsilastirmada
   sessizce "eslesti" gibi davranabilecek degerdir; "bilinmiyor" asla eslesmez.
"""
import re
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
MAIN = (KOK / "main.py").read_text(encoding="utf-8")
BUILDSPEC = (KOK / "buildspec.yml").read_text(encoding="utf-8")


def test_health_commit_doner():
    i = MAIN.index("async def health()")
    assert '"commit": _KOSAN_COMMIT' in MAIN[i:i + 400]


def test_buildspec_SHAyi_imaja_yaziyor():
    """Uc de dogru olmali: dosya adi, degisken, docker build'DEN ONCE."""
    assert 'echo "$SHA" > SURUM.txt' in BUILDSPEC
    assert BUILDSPEC.index("SURUM.txt") < BUILDSPEC.index("docker build"), \
        "SURUM.txt docker build'den SONRA yaziliyor — imaja girmez"


def test_dosya_yoksa_BOS_DIZE_donmez():
    """🪤 Bos dize `sha in yanit` karsilastirmasinda her zaman True olurdu."""
    i = MAIN.index("def _kosan_commit")
    govde = MAIN[i:i + 700]
    assert 'return "bilinmiyor"' in govde
    assert re.search(r'return\s+""', govde) is None


def test_SURUM_txt_gitignorede():
    """Yerel kosuda uretilirse kazara commit'lenip yanlis SHA yayinlamasin."""
    assert "SURUM.txt" in (KOK / ".gitignore").read_text(encoding="utf-8")
