"""
Dinamik paylasim karti (OG image) — viral yayilim icin.

geoni.ai/s/<id> paylasilinca X/LinkedIn/WhatsApp/Slack/iMessage feed'inde gorunen
gorsel. ESKI: herkes ayni statik og-share.png'yi goruyordu. YENI: kisinin GERCEK
skoru + adiyla kisisel kart → tiklamadan once bile "statu nesnesi" → cok daha iyi yayilir.

1200x630 PNG (OG standardi). Pillow reportlab ile zaten kurulu. Fontlar Playwright
Ubuntu image'inda (DejaVu). Font yoksa load_default'a duser (kart yine cizilir).
"""
from __future__ import annotations

import glob
import io
import logging
import os

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
]
_REG = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
]


def _first_glob(*patterns: str) -> str | None:
    for pat in patterns:
        hits = glob.glob(pat, recursive=True)
        if hits:
            return sorted(hits)[0]
    return None


def _resolve_font_path(bold: bool) -> str | None:
    # 1) bilinen tam yollar
    for p in (_BOLD if bold else _REG):
        if os.path.exists(p):
            return p
    # 2) tum font agacinda ara (load_default'a — bitmap, olceklenmez — DUSME)
    key = "Bold" if bold else "Regular"
    return (
        _first_glob(
            f"/usr/share/fonts/**/DejaVuSans{'-Bold' if bold else ''}.ttf",
            f"/usr/share/fonts/**/LiberationSans-{key}.ttf",
            "/usr/share/fonts/**/DejaVuSans*.ttf",
            "/usr/share/fonts/**/*.ttf",
        )
    )


def _font(size: int, bold: bool = False):
    path = _resolve_font_path(bold)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


# Skor renk esikleri — ISTEMCILERLE AYNI OLMAK ZORUNDA.
#
# 🪤 2026-08-07'de olculdu: kart 70 kullaniyordu, web ve mobil 65. Ayni skor
# (or. 67) UYGULAMADA YESIL, paylasilan KARTTA AMBER cikiyordu — kullanicinin
# X/LinkedIn/WhatsApp'ta gosterdigi kart, uygulamada gordugunden DAHA KOTU
# gorunuyordu. Kart bir "statu nesnesi" olsun diye var; bu tam tersini yapiyor.
# Tamamlanan 437 taramanin 45'i (%10,3) bu araliga dusuyordu.
#
# Web/mobil 2026-08-04'te 65'te birlestirilmisti (frontend `lib/skor.js`,
# mobil `lib/skor.ts`) — kart o denetimde atlanmis. Uc istemci artik ayni.
# 70 AYRI bir esiktir (muhur/rozet hakki) ve buradan TURETILMEZ.
SKOR_IYI = 65
SKOR_ORTA = 40


def _score_color(score: int) -> tuple[int, int, int]:
    if score >= SKOR_IYI:
        return (63, 185, 132)   # yesil
    if score >= SKOR_ORTA:
        return (245, 166, 35)   # amber
    return (240, 97, 109)       # kirmizi


def render_score_card(label: str, score: float, atype: str = "web", lang: str = "tr") -> bytes:
    """Skor kartini PNG bayt olarak dondurur (1200x630)."""
    W, H = 1200, 630
    s = int(round(max(0.0, min(100.0, float(score)))))
    color = _score_color(s)
    en = (lang == "en")

    img = Image.new("RGB", (W, H), (10, 11, 16))          # --bg
    d = ImageDraw.Draw(img)

    # ince ust aksan cizgisi
    d.rectangle([0, 0, W, 6], fill=(124, 134, 245))

    # marka + etiket (sol ust)
    d.text((70, 62), "GEONI", font=_font(40, True), fill=(237, 239, 245))
    tag = ("AI VISIBILITY" if en else "AI GÖRÜNÜRLÜK")
    d.text((72, 118), tag, font=_font(20, True), fill=(124, 134, 245))

    # ad/domain (sol, kirpilmis)
    lbl = (label or "").strip()
    if len(lbl) > 26:
        lbl = lbl[:25] + "…"
    d.text((70, 250), lbl, font=_font(56, True), fill=(237, 239, 245))
    caption = ("AI Visibility Score" if en else "AI Görünürlük Skoru")
    d.text((72, 330), caption, font=_font(28, False), fill=(155, 163, 181))

    # buyuk skor (sag)
    sf = _font(210, True)
    stext = str(s)
    sw = d.textlength(stext, font=sf)
    sx = W - 90 - sw
    d.text((sx, 200), stext, font=sf, fill=color)
    d.text((sx + sw + 6, 350), "/100", font=_font(40, True), fill=(110, 115, 145))

    # ilerleme cubugu
    bx, by, bw, bh = 70, 452, W - 140, 26
    d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=13, fill=(30, 33, 45))
    fillw = int(bw * max(s, 3) / 100)
    d.rounded_rectangle([bx, by, bx + fillw, by + bh], radius=13, fill=color)

    # CTA (alt)
    cta = ("Measure yours → geoni.ai" if en else "Sen de ölç → geoni.ai")
    d.text((70, 528), cta, font=_font(34, True), fill=(237, 239, 245))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
