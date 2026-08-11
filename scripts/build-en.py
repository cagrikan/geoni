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
        # 🪤 Bir eleman BIRDEN COK data-i18n* niteligi tasiyabilir (or. hem -ph hem
        # -aria). Eski surum regex'in yakaladigi ILK nitelige bakiyordu; ikincisi
        # sessizce Turkce kaliyordu. 2026-08-06'da yakalandi: #audit-input hem
        # data-i18n-ph hem data-i18n-aria tasiyor, Ingilizce sayfada aria-label
        # "Yanitinizi yazin" olarak kaliyordu (ekran okuyucu icin gorunmez hata,
        # gozle fark edilmiyor). Artik etiketteki TUM nitelikler uygulanir.
        cesitler = [(t or "", k) for t, k in
                    re.findall(r"data-i18n(-ph|-opt|-aria|-list)?=\"([^\"]+)\"", m.group(0))]
        # Once ICERIK tipleri: bunlar etiketin SONRASINI degistirir, boylece
        # [m.start(), m.end()) araligi gecerli kalir ve nitelikler sonra yazilabilir.
        icerik = [(t, k) for t, k in cesitler if t in ("", "-opt", "-list")]
        nitelik = [(t, k) for t, k in cesitler if t in ("-ph", "-aria")]

        if nitelik:
            etiket = m.group(0)
            for tur, anahtar in nitelik:
                if anahtar not in en:
                    eksik.append(anahtar)
                    continue
                ad = "placeholder" if tur == "-ph" else "aria-label"
                etiket = _nitelik_yaz(etiket, ad, en[anahtar])
            bekleyen_etiket = etiket
        else:
            bekleyen_etiket = None

        for tur, anahtar in icerik:
            if anahtar not in en:
                eksik.append(anahtar)
                continue
            deger = en[anahtar]
            aralik = _ic_aralik(html, m.start())
            if aralik is None:
                continue
            ib, iso = aralik
            if tur == "":                  # innerHTML
                html = html[:ib] + deger + html[iso:]
            elif tur == "-opt":            # textContent (option)
                metin = (deger.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
                html = html[:ib] + metin + html[iso:]
            elif tur == "-list":           # <li> dizisi
                if not isinstance(deger, list):
                    continue
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

        # Nitelikler EN SONDA yazilir: icerik degisiklikleri etiketin sonrasini
        # oynattigi icin [m.start(), m.end()) araligi hala gecerlidir.
        if bekleyen_etiket is not None:
            html = html[:m.start()] + bekleyen_etiket + html[m.end():]
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

        # ── FAQ JSON-LD (2026-08-12) ────────────────────────────────────────
        # 🪤 Bu uc soru/cevap Ingilizce belgede TURKCE kaliyordu. GEO urununun
        # AI'a okuttugu katmanin yanlis dilde olmasi, tam da sattigimiz seyin
        # tersi. Once `en.html` ELLE duzeltilmisti ama en.html URETILEN bir
        # dosya — jenerator bir sonraki kosuda ceviriyi geri aldi ve CI kirmizi
        # yandi ("en.html guncel degil"). Duzeltme buraya, kaynaga yazilir.
        # Metinler sayfanin GORUNEN Ingilizce SSS'iyle birebir ayni olmali.
        "SEO'da iyiyim — GEO'da da iyi miyimdir?":
            "I'm good at SEO — am I good at GEO too?",
        "Çoğu zaman hayır. AI motorları sıralamaya değil; bot erişiminize, "
        "yapılandırılmış verinize, varlık kayıtlarınıza (bilgi grafikleri) ve "
        "bağımsız atıflarınıza bakar. Google'da 1. sıradaki bir site, ChatGPT "
        "cevabında hiç görünmeyebilir.":
            "Usually no. AI engines don't look at rankings; they look at your bot "
            "access, structured data, entity records (knowledge graphs) and "
            "independent citations. A site ranked #1 on Google can be entirely "
            "absent from a ChatGPT answer.",
        "GEO ne kadar sürede sonuç verir?": "How fast does GEO show results?",
        "Teknik katman (bot erişimi, şema) günler içinde ölçülebilir etki "
        "yaratır; atıf ve bilinirlik katmanı haftalar içinde oturur. İkisini de "
        "aynı panelden, skor değişimiyle takip edersiniz.":
            "The technical layer (bot access, schema) shows measurable impact "
            "within days; the citation and recognition layer settles within weeks. "
            "You track both from one panel, via your score.",
        "Nereden başlamalıyım?": "Where should I start?",
        "Birkaç dakikalık ücretsiz taramayla. AI Görünürlük Skorunuz ve eksik "
        "listeniz çıkar; dilediğiniz eksiği tek tıkla uzmanlarımıza devredersiniz.":
            "With the free scan (a few minutes). You get your AI Visibility Score "
            "and gap list; hand any gap to our experts in one click.",

        # Kurucu `Person` dugumu de Turkce kaliyordu.
        "Yatırımcı & Stratejik Danışman": "Investor & Strategic Advisor",
        '"Yapay Zeka",': '"Artificial Intelligence",',
        '"Kurumsal Bilişim",': '"Enterprise IT",',
        '"Dijital Dönüşüm"': '"Digital Transformation"',
    }
    for tr, en in degisim.items():
        html = html.replace(tr, en)
    return html


DUGME_TR = ('<a class="nav-toggle-btn" id="lang-toggle" href="/en" hreflang="en" '
            'title="English" aria-label="İngilizce sürüme geç">EN</a>')
DUGME_EN = ('<a class="nav-toggle-btn" id="lang-toggle" href="/" hreflang="tr" '
            'title="Türkçe" aria-label="Switch to Turkish">TR</a>')


def dil_dugmesi(html: str) -> str:
    """Ingilizce sayfada dil degistirici Turkce ana sayfaya bakmali.

    NEDEN: dugme artik gercek bir <a href> (bkz. index.html'deki yorum). Ham
    HTML'i okuyan AI tarayicisi burayi gordugu icin hedefi de metni de dogru
    olmali; yoksa /en sayfasi kendi kendine baglanir ve `/` yetim kalir.
    """
    if DUGME_TR not in html:
        raise ValueError("dil degistirici baglantisi bulunamadi — index.html'de "
                         "markup degisti mi? build-en.py'deki DUGME_TR guncellenmeli.")
    return html.replace(DUGME_TR, DUGME_EN, 1)


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
