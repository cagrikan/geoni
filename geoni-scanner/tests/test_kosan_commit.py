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
    """Damga docker build'DEN ONCE yazilmali, yoksa imaja girmez."""
    assert "SURUM.txt" in BUILDSPEC
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


# ── EC2 yedek yolu buildspec'i HIC calistirmaz ───────────────────────────
# deploy.sh CodeBuild kotasi kapaliysa tek seferlik EC2 makinesinde kendi
# `docker build`ini kosuyor. Damgayi yalniz buildspec'e koysaydik o yoldan
# cikan imaj "bilinmiyor" derdi ve dogrulama sessizce ise yaramazdi.
DEPLOY = (KOK / "deploy.sh").read_text(encoding="utf-8")


def test_deploy_sh_SHAyi_zipten_ONCE_yaziyor():
    assert "> SURUM.txt" in DEPLOY
    assert DEPLOY.index("> SURUM.txt") < DEPLOY.index("zip -qr"), \
        "SURUM.txt zip'ten SONRA yaziliyor — kaynak paketine girmez"


def test_kirli_depo_ISARETLENIYOR():
    """Commit'lenmemis degisiklikle dagitilirsa SHA yalan soyler."""
    assert "-kirli" in DEPLOY


def test_damga_TEK_SATIR():
    """🪤 Ikinci satir eklenirse /health'in dondugu deger satir sonu tasir."""
    i = DEPLOY.index("SHA_YEREL=")
    blok = DEPLOY[i:i + 300]
    assert blok.count("> SURUM.txt") == 1, "SURUM.txt'e birden fazla yazma var"


def test_buildspec_deploy_shin_damgasini_EZMIYOR():
    assert "[ -s SURUM.txt ] ||" in BUILDSPEC


def test_immutable_etiket_gercek_SHA():
    """S3 kaynakli build'de $SHA hep 'manual'di; etiket her seferinde eziliyordu."""
    assert "ETIKET=$(cat SURUM.txt)" in BUILDSPEC
    assert "-t $ECR/$REPO:$ETIKET" in BUILDSPEC
