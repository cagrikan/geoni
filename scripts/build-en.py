#!/usr/bin/env python3
"""index.html -> en.html (Ingilizce statik on-render).

NEDEN: index.html'in Ingilizce cevirisi zaten var ama YALNIZCA JavaScript'te
(`I18N.en` sozlugu + `data-i18n` nitelikleri). Tarayici cevirdigi icin kullanici
Ingilizce goruyor; ama GPTBot/ClaudeBot/PerplexityBot gibi AI tarayicilari ve
sunucu tarafi cekimler HTML'i ham haliyle aliyor -> onlara sayfa TAMAMEN
TURKCE gorunuyordu. 2026-08-01 olcumu: geoni.ai Ingilizce taramasinda 5
motordan 4'u markayi hic tanimadi (dogrulanmis olgu sayisi 0).

Bu betik sozlugu DERLEME ANINDA uygulayarak /en icin gercek, ham HTML'de
Ingilizce bir belge uretir. Ceviriler tek yerde (index.html'deki I18N.en)
kalir; en.html elle duzenlenmez, hep buradan yeniden uretilir.

Kullanim:  python3 scripts/build-en.py   (proje kokunden)
"""
import json
import pathlib
import re
import sys

KOK = pathlib.Path(__file__).resolve().parent.parent
KAYNAK = KOK / "index.html"
HEDEF = KOK / "en.html"

# Ingilizce sayfada Turkce sayfalara giden ic baglantilarin karsiliklari.
# (Sag taraf yoksa baglanti oldugu gibi kalir.)
LINK_ESLEME = {"/rehber": "/guides"}

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr"}


def sozluk_oku(html: str) -> dict:
    """index.html icindeki `const I18N = {...}` blogunu JSON olarak cozer."""
    bas = html.index("const I18N = {")
    # Sozluk `\n};` ile biter. Yorum satirlari sonradan eklenebildigi icin
    # "sonraki degisken" yerine bu kapanisa gore kesiyoruz.
    son = html.index("\n};", bas) + len("\n};")
    blok = html[bas + len("const I18N = "):son].rstrip().rstrip(";")
    return json.loads(blok)


def _etiket_adi(html: str, i: int) -> str:
    m = re.match(r"<([a-zA-Z][a-zA-Z0-9-]*)", html[i:])
    return m.group(1).lower() if m else ""


def _ic_aralik(html: str, bas: int):
    """Acilis etiketi `bas`ta olan elemanin ic HTML aralgini (ic_bas, ic_son) dondurur.

    NEDEN elle tarayici: bs4/lxml bu ortamda yok ve tam belge yeniden
    serilestirilirse 36 KB inline CSS + 75 KB inline JS'te sessiz bozulma
    riski var. Burada YALNIZ ilgili elemanin ici degistirilir, geri kalan
    bayt bayt ayni kalir.
    """
    ad = _etiket_adi(html, bas)
    ac_son = html.index(">", bas)
    if html[ac_son - 1] == "/" or ad in VOID:
        return None
    derinlik = 1
    p = ac_son + 1
    kalip = re.compile(rf"<(/?){re.escape(ad)}(?=[\s/>])", re.I)
    while derinlik:
        m = kalip.search(html, p)
        if not m:
            raise ValueError(f"<{ad}> kapanisi bulunamadi (offset {bas})")
        derinlik += -1 if m.group(1) else 1
        p = m.end()
    return ac_son + 1, html.rindex("<", ac_son + 1, p)


def _nitelik_yaz(etiket: str, ad: str, deger: str) -> str:
    """Acilis etiketinde bir niteligin degerini degistirir/ekler."""
    kacis = deger.replace("&", "&amp;").replace('"', "&quot;")
    if re.search(rf'\s{ad}="', etiket):
        return re.sub(rf'(\s{ad}=")[^"]*(")', lambda m: m.group(1) + kacis + m.group(2), etiket, count=1)
    return etiket[:-1].rstrip("/") + f' {ad}="{kacis}">'


def cevir(html: str, en: dict) -> tuple[str, list]:
    """data-i18n* niteliklerini tasiyan elemanlari Ingilizce sozlukle doldurur."""
    eksik = []
    # Sondan basa git: offsetler kaymasin.
    isaretler = [m for m in re.finditer(r"<[a-zA-Z][^>]*?data-i18n(-ph|-opt|-aria|-list)?=\"([^\"]+)\"[^>]*>", html)]
    for m in reversed(isaretler):
        tur, anahtar = m.group(1) or "", m.group(2)
        if anahtar not in en:
            eksik.append(anahtar)
            continue
        deger = en[anahtar]
        if tur == "":                      # innerHTML
            aralik = _ic_aralik(html, m.start())
            if aralik is None:
                continue
            ib, iso = aralik
            html = html[:ib] + deger + html[iso:]
        elif tur == "-opt":                # textContent (option)
            aralik = _ic_aralik(html, m.start())
            if aralik is None:
                continue
            ib, iso = aralik
            metin = (deger.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
            html = html[:ib] + metin + html[iso:]
        elif tur == "-ph":
            html = html[:m.start()] + _nitelik_yaz(m.group(0), "placeholder", deger) + html[m.end():]
        elif tur == "-aria":
            html = html[:m.start()] + _nitelik_yaz(m.group(0), "aria-label", deger) + html[m.end():]
        elif tur == "-list":               # <li> dizisi
            aralik = _ic_aralik(html, m.start())
            if aralik is None or not isinstance(deger, list):
                continue
            ib, iso = aralik
            ic = html[ib:iso]
            sayac = [0]
            parcalar, p = [], 0
            for mm in re.finditer(r"<li(?=[\s>])", ic):
                a = _ic_aralik(ic, mm.start())
                if a is None or sayac[0] >= len(deger):
                    continue
                parcalar.append(ic[p:a[0]])
                parcalar.append(deger[sayac[0]])
                sayac[0] += 1
                p = a[1]
            parcalar.append(ic[p:])
            html = html[:ib] + "".join(parcalar) + html[iso:]
    return html, eksik


def kafa_duzelt(html: str) -> str:
    """lang / title / meta / canonical / hreflang -> Ingilizce surum."""
    html = html.replace('<html lang="tr"', '<html lang="en"', 1)
    html = re.sub(r"<title>.*?</title>", "<title>GEONI — AI Visibility Optimization</title>",
                  html, count=1, flags=re.S)

    def meta_yaz(h, sec, deger):
        return re.sub(rf'(<meta {sec}[^>]*content=")[^"]*(")',
                      lambda m: m.group(1) + deger + m.group(2), h, count=1)

    html = meta_yaz(html, 'name="description"',
                    "GEO services that make your brand visible in ChatGPT, Perplexity, Claude and "
                    "Gemini. Measure your AI visibility with a free scan.")
    html = meta_yaz(html, 'property="og:title"', "GEONI — Does AI know you?")
    html = meta_yaz(html, 'property="og:description"',
                    "Measure how ChatGPT, Gemini, Claude and Perplexity see your brand, name or "
                    "site in minutes — free.")
    html = meta_yaz(html, 'property="og:locale"', "en_US")
    html = meta_yaz(html, 'property="og:locale:alternate"', "tr_TR")
    html = meta_yaz(html, 'property="og:url"', "https://geoni.ai/en")
    html = meta_yaz(html, 'name="twitter:title"', "GEONI — Does AI know you?")
    html = meta_yaz(html, 'name="twitter:description"',
                    "Measure how ChatGPT, Gemini, Claude and Perplexity see your brand, name or "
                    "site in minutes — free.")
    html = re.sub(r'(<link rel="canonical" href=")[^"]*(")',
                  lambda m: m.group(1) + "https://geoni.ai/en" + m.group(2), html, count=1)
    return html


def yapisal_veri_ingilizce(html: str) -> str:
    """JSON-LD'deki Turkce aciklamalari Ingilizce karsiliklariyla degistirir.

    NEDEN: /guides sayfalari `@id: …/#organization` diyor ama o dugum yalnizca
    Turkce ana sayfada tanimliydi; Ingilizce belgede varlik cozulemiyordu.
    """
    degisim = {
        "GEONI, markaların ChatGPT, Perplexity, Claude ve Gemini gibi yapay zeka motorlarında "
        "görünürlüğünü optimize eden Generative Engine Optimization (GEO) platformudur.":
            "GEONI is a Generative Engine Optimization (GEO) platform that measures and improves "
            "how brands, people and websites appear inside the answers of AI engines such as "
            "ChatGPT, Claude, Gemini and Perplexity.",
        "Markaların, kişilerin ve web sitelerinin AI cevap motorlarındaki (ChatGPT, Claude, "
        "Gemini, Perplexity) görünürlüğünü dakikalar içinde ölçen araç.":
            "Tool that measures, in minutes, how visible a brand, a person or a website is inside "
            "AI answer engines (ChatGPT, Claude, Gemini, Perplexity) and reports what is missing.",
        "Ücretsiz AI görünürlük taraması": "Free AI visibility scan",
    }
    for tr, en in degisim.items():
        html = html.replace(tr, en)
    return html


def dil_dugmesi(html: str) -> str:
    """Ingilizce sayfada dil dugmesi 'TR' yazmali. JS zaten DOMContentLoaded'da
    duzeltiyor ama ham HTML'i okuyan AI tarayicisi/JS'siz istemci 'EN' goruyordu."""
    return html.replace('id="lang-toggle" title="Language" aria-label="Dil degistir">EN<',
                        'id="lang-toggle" title="Dil" aria-label="Switch language">TR<', 1)


def baglanti_esle(html: str) -> str:
    for tr, en in LINK_ESLEME.items():
        html = html.replace(f'href="{tr}"', f'href="{en}"')
    return html


def main() -> int:
    kaynak = KAYNAK.read_text(encoding="utf-8")
    en = sozluk_oku(kaynak)["en"]

    html, eksik = cevir(kaynak, en)
    html = kafa_duzelt(html)
    html = yapisal_veri_ingilizce(html)
    html = dil_dugmesi(html)
    html = baglanti_esle(html)

    if eksik:
        print(f"UYARI: sozlukte olmayan anahtarlar: {sorted(set(eksik))}", file=sys.stderr)

    HEDEF.write_text(html, encoding="utf-8")
    print(f"{HEDEF.name} yazildi ({len(html)} B, kaynak {len(kaynak)} B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
