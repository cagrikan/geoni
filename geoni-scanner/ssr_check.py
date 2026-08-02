"""
GEONI Scanner — SSR (sunucu tarafi render) korlugu olcumu.

NEDEN VAR: crawler.py Playwright + Chromium kullaniyor, yani JAVASCRIPT
CALISTIRIYOR. AI tarayicilari (GPTBot, ClaudeBot, PerplexityBot) calistirmaz.
Sonuc: icerigi yalnizca JS sonrasi olusan bir sitede BIZ dolu sayfa goruyoruz,
AI botu bos kabuk. Sema buluyoruz, metin sayiyoruz, tazelik hesapliyoruz —
hicbirini AI goremiyor. Musteriye "teknik tarafin iyi" diyoruz, gercekte
gorunmez. Bu, skoru YANLIS YONDE bozan tek bulgu: eksik olcum degil, SAHTE
GUVEN uretiyor.

Olculdu (2026-08-02, GPTBot user-agent'i ile, JS'siz ham HTML):
    app.geoni.ai  ->   2.038 bayt HTML,   175 karakter gorunen metin  (SPA)
    geoni.ai      -> 147.914 bayt HTML, 7.818 karakter gorunen metin  (statik)

NEDEN AYRI MODUL: crawler.py playwright istiyor; CI deploy kapisi minimal
ortam kuruyor (pytest httpx cbor2 cryptography). Mantik burada durursa
playwright'siz test edilebilir ve kapiyi kirmaz (bkz. task_protection.py'de
yasanan boto3 olayi).
"""

import asyncio
import logging
import re

import httpx

from ssrf_guard import safe_get

logger = logging.getLogger(__name__)

# AI tarayicilarinin cogu boyle tanitir kendini. Sunucu bot'a FARKLI icerik
# veriyorsa (cloaking ya da bot korumasi) bunu gormek istiyoruz — tarayici
# user-agent'i ile sorsaydik o farki kaciririrdik.
_UA = "Mozilla/5.0 (compatible; GPTBot/1.2; +https://openai.com/gptbot)"

# Ornek sayfa sayisi: render stratejisi site genelinde neredeyse her zaman
# aynidir; 5 sayfa gercegi verir, 50 sayfa yalnizca istek yakar.
SAMPLE_SIZE = 5

_TIMEOUT = 12.0

# Bu oranin ALTI "AI botu sayfanin cogunu goremiyor" sayilir. 0.35 seciminin
# gerekcesi: statik sitelerde ham/render orani ~0.9-1.0 civari (geoni.ai:
# 7818 gorunen metin, ham HTML'de de ayni metin var). Saf SPA'da oran ~0.02
# (app.geoni.ai: 175 / ~9000). Aradaki bosluk genis; 0.35 hibrit sayfalari
# (kismi SSR + JS ile zenginlestirme) cezalandirmadan saf SPA'yi yakalar.
DUSUK_ESIK = 0.35

_ETIKET_RE = re.compile(r"(?is)<(script|style|noscript|template)[^>]*>.*?</\1>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_YORUM_RE = re.compile(r"(?s)<!--.*?-->")


def gorunen_metin(html: str) -> str:
    """HTML'den kullanicinin/botun GORDUGU metni cikarir.

    Yorumlar ONCE atilir: app.geoni.ai olcumunde gorunen 175 karakterin
    buyuk kismi bir HTML YORUMUYDU (performans notu). Yorum sayilsaydi bos
    bir SPA'yi 'icerigi var' diye olcerdik.
    """
    if not html:
        return ""
    s = _YORUM_RE.sub(" ", html)
    s = _ETIKET_RE.sub(" ", s)
    s = _TAG_RE.sub(" ", s)
    return " ".join(s.split())


async def _tek_sayfa(client, url: str, render_len: int) -> dict | None:
    try:
        r = await safe_get(client, url, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        ham = len(gorunen_metin(r.text))
    except Exception as e:
        logger.warning(f"SSR ham getirme hatasi ({url[:60]}): {e}")
        return None
    # render_len 0 ise oran anlamsiz (sayfa gercekten bos) — olcumden cikar.
    if render_len <= 0:
        return None
    return {"url": url, "raw_len": ham, "render_len": render_len,
            "ratio": round(min(1.0, ham / render_len), 3)}


async def check_ssr(sayfalar: list[dict]) -> dict | None:
    """
    `sayfalar`: [{"url": str, "text_len": int}, ...] — crawler'in render
    SONRASI olctugu gorunen metin uzunluklariyla.

    Doner:
      {"sampled": int, "median_ratio": float, "js_dependent": bool,
       "hidden_pct": int, "pages": [...]}
    Olcum yapilamazsa None (ozellik sessizce yok sayilir, tarama bozulmaz).
    """
    aday = [p for p in (sayfalar or [])
            if p.get("url") and isinstance(p.get("text_len"), int) and p["text_len"] > 0]
    if not aday:
        return None
    ornek = aday[:SAMPLE_SIZE]

    async with httpx.AsyncClient() as client:
        sonuc = await asyncio.gather(*[
            _tek_sayfa(client, p["url"], p["text_len"]) for p in ornek
        ])
    olculen = [s for s in sonuc if s]
    if not olculen:
        return None

    oranlar = sorted(s["ratio"] for s in olculen)
    orta = oranlar[len(oranlar) // 2]          # medyan: tek bir aykiri sayfa sonucu savurmasin
    return {
        "sampled": len(olculen),
        "median_ratio": orta,
        "js_dependent": orta < DUSUK_ESIK,
        # Rapor metni icin: "AI botlari bu sayfanin %X'ini goremiyor"
        "hidden_pct": int(round((1 - orta) * 100)),
        "pages": olculen,
    }
