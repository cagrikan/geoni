"""
"AI Botlarına Erişim İzni" (llms_robots) bileti - tek 5 hizmet arasında
insan uzman gerektirmeyecek kadar basit: robots.txt + llms.txt icerigi
domain bilgisinden (ve varsa GEONI'nin kendi taramasindan) deterministik
olarak uretilebilir. Diger 4 hizmet (sema/wikidata/icerik/atif) gercek
arastirma/muhakeme gerektirdigi icin insan uzmanda kalir.

Otomasyon, uretilen dosyalari SYSTEM yazari olarak konusma akisina ekleyip
bileti 'submitted' durumuna ceker. C-1 NOT: dosyalar musteriye ANINDA gorunur
(thread'e dusen system mesajlari gizlenmiyor); 'admin onayi' bir ON-KAPI DEGIL,
teslim sonrasi bir kalite denetimidir. Bu yuzden musteriye giden mesajlar
"otomatik olusturuldu / gorunur" diye dururst ifade edilir, "kalite kontrolden
SONRA iletilecek" gibi bir gecikme yanilsamasi verilmez.
"""
import json
import logging
import re
from datetime import date
from urllib.robotparser import RobotFileParser

import httpx

from ssrf_guard import safe_get
from indexing import TRAINING_CRAWLER_AGENTS, SEARCH_CRAWLER_AGENTS
from db import (
    get_latest_web_audit_by_domain, list_ticket_tasks, toggle_ticket_task,
    add_ticket_message, get_ticket_type_by_key, mark_ticket_submitted,
    upload_ticket_file, normalize_domain,
)

logger = logging.getLogger(__name__)

# Tum AI botlari (uretim + canli kontrol AYNI kaynaktan beslenir — tek dogruluk).
_AI_AGENTS = list(dict.fromkeys(
    list(SEARCH_CRAWLER_AGENTS.values()) + list(TRAINING_CRAWLER_AGENTS.values())
))
_UA = {"User-Agent": "GEONI-bot/1.0 (+https://geoni.ai)"}
# llms.txt sayfa gurultusu: giris/sepet/hesap/yonetim/etiket/sayfalama/querystring.
_PAGE_NOISE_RE = re.compile(
    r"(login|signin|sign-in|/cart|/sepet|checkout|/account|/hesab|/admin|/wp-|"
    r"/tag/|/etiket/|/page/\d|/sayfa/\d|privacy|gizlilik|cerez|/kvkk|\?)", re.I)


def _sanitize_text(s: str, max_len: int) -> str:
    """B-7: title/meta/inferred_* musterinin (ya da saldirganin) kontrolundeki
    HTML'den gelir; teslim dosyasina filtresiz akmamali. Satir sonu + markdown/
    HTML kontrol karakterleri temizlenir, uzunluk sinirlanir."""
    s = re.sub(r"[\r\n\t]+", " ", s or "")
    s = re.sub(r"[\[\]()<>`#>|{}]", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s[:max_len].rstrip()


def _same_site_url(url: str, domain: str) -> bool:
    """URL yalniz hedef domain (ya da subdomain) ise teslim dosyasina girer —
    sayfa listesine sizmis harici link cikmaz."""
    u = (url or "").strip().lower()
    if not u.startswith(("http://", "https://")):
        return False
    host = re.sub(r"^https?://", "", u).split("/")[0].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host == domain or host.endswith("." + domain)


def _curate_pages(pages: list, domain: str) -> list[dict]:
    """llms.txt icin sayfa kuratorlugu: gurultu at, tekrarsizlastir, sirala.
    Envanter degil, kuratorlu icindekiler (spec + saha best-practice)."""
    seen, out = set(), []
    home = {f"https://{domain}", f"https://{domain}/", f"https://www.{domain}", f"https://www.{domain}/"}
    for p in pages or []:
        if not isinstance(p, dict):
            continue
        url = (p.get("url") or "").strip()
        title = _sanitize_text(p.get("title") or "", 80)
        if not url or not title or not _same_site_url(url, domain):
            continue
        if _PAGE_NOISE_RE.search(url):
            continue
        key = re.sub(r"[?#].*$", "", url).rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append({"url": url, "title": title,
                    "meta": _sanitize_text(p.get("meta_description") or "", 200),
                    "is_home": url in home, "depth": key.count("/")})
    out.sort(key=lambda x: (0 if x["is_home"] else 1, 0 if x["meta"] else 1, x["depth"]))
    return out


async def _sitemap_exists(domain: str, audit: dict | None) -> bool:
    """B-4: Sitemap: satiri yalniz KANITLA yazilir. Once audit'in bildigini
    kullan (crawl sitemap_found dondurur), yoksa canli kontrol (soft-404 HTML red)."""
    if audit:
        sf = (audit.get("result_json") or {}).get("sitemap_found")
        if sf is not None:
            return bool(sf)
    for path in ("/sitemap.xml", "/sitemap_index.xml"):
        try:
            async with httpx.AsyncClient() as c:
                r = await safe_get(c, f"https://{domain}{path}", timeout=8, headers=_UA)
            body = (r.text or "")[:2000].lower()
            if r.status_code == 200 and ("<urlset" in body or "<sitemapindex" in body or "<loc>" in body):
                return True
        except Exception:
            continue
    return False


def _extract_star_disallows(robots_text: str) -> list[str]:
    """Mevcut robots.txt'te 'User-agent: *' grubunun Disallow satirlarini cikarir;
    GEONI blogu bu botlari '*' grubundan cikardigi icin hassas path'ler bloga
    kopyalanir (musterinin wp-admin/staging korumasi AI botlarda da korunur)."""
    dis, in_star = [], False
    for raw in (robots_text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("user-agent:"):
            in_star = line.split(":", 1)[1].strip() == "*"
        elif in_star and low.startswith("disallow:"):
            val = line.split(":", 1)[1].strip()
            if val:
                dis.append(val)
    return list(dict.fromkeys(dis))

# B-5: otomasyon YALNIZCA dosya uretimini yapar; "siteye ekle/doğrula/yayınla"
# gibi adimlar MUSTERININ sitesinde olur, otomasyon bunlari YAPMAZ. Bu ipuclarini
# iceren gorevleri "yapildi" diye isaretlemek yanlis beyandir (checklist + Is
# Teslim Raporu). Bu gorevler isaretsiz kalir; musteri "ekledim" deyince canli
# check_robots_ai_access/check_llms_txt ile dogrulanabilir.
_ONSITE_TASK_CUES = (
    "doğrula", "dogrula", "ekle", "yükle", "yukle", "yayınla", "yayinla",
    "sitenize", "siteye", "canlı", "canli", "kök dizin", "kok dizin",
    "verify", "upload", "publish", "add to", "deploy", "live",
)


def _is_auto_completable(title: str) -> bool:
    """Gorev otomasyonun GERCEKTEN yaptigi bir sey mi (uretim), yoksa musterinin
    sitesinde yapmasi gereken bir adim mi? On-site adimlar isaretlenmez (B-5)."""
    t = (title or "").lower()
    return not any(c in t for c in _ONSITE_TASK_CUES)


async def generate_robots_txt(domain: str, sitemap_ok: bool = False) -> tuple[str, str]:
    """B-2: robots.txt'i ASLA korlemesine uretme — canli dosyayi cek, mevcut
    kurallari KORU, AI botlarina erisim blogunu EKLE. Doner: (metin, durum).
    durum: 'merged' | 'already_open' (butun AI botlari zaten izinli) | 'created'
    (mevcut cekilemedi -> yeni dosya, cagiran 'uzerine yazmayin' uyarisi verir)."""
    existing = None
    try:
        async with httpx.AsyncClient() as c:
            r = await safe_get(c, f"https://{domain}/robots.txt", timeout=8, headers=_UA)
        body = r.text or ""
        # soft-404: SPA her path'e 200 + index.html doner -> robots sayma.
        if r.status_code == 200 and body.strip() and "<html" not in body[:600].lower():
            existing = body
    except Exception:
        existing = None

    block_lines = [
        f"# --- GEONI: AI botlarina erisim izni (eklendi: {date.today().isoformat()}) ---",
        "# Bu bloklar yapay zeka arama/alintilama botlarina site erisimi verir.",
    ]
    block_lines += [f"User-agent: {agent}" for agent in _AI_AGENTS]
    # Mevcut '*' grubunun Disallow'larini AI botlara da tasi: GEONI blogu bu
    # botlari '*'tan cikardigindan, musterinin hassas path'leri (wp-admin, staging)
    # AI botlarda da korunmali.
    for d in (_extract_star_disallows(existing) if existing else []):
        block_lines.append(f"Disallow: {d}")
    block_lines.append("Allow: /")
    block = "\n".join(block_lines)

    if existing is None:
        header = (
            "# robots.txt — GEONI tarafindan olusturuldu.\n"
            "# DIKKAT: Sitenizde ZATEN robots.txt VARSA bu dosyayla DEGISTIRMEYIN;\n"
            "# yalnizca asagidaki '--- GEONI' bolumunu mevcut dosyanizin SONUNA ekleyin.\n"
        )
        out = header + "\nUser-agent: *\nAllow: /\n\n" + block
        if sitemap_ok:
            out += f"\n\nSitemap: https://{domain}/sitemap.xml"
        return out + "\n", "created"

    rp = RobotFileParser()
    rp.parse(existing.splitlines())
    if all(rp.can_fetch(agent, "/") for agent in _AI_AGENTS):
        return existing.rstrip() + "\n", "already_open"
    merged = existing.rstrip() + "\n\n" + block
    if sitemap_ok and "sitemap:" not in existing.lower():
        merged += f"\n\nSitemap: https://{domain}/sitemap.xml"
    return merged + "\n", "merged"


def generate_llms_txt(domain: str, audit: dict | None) -> str:
    """llms.txt (llmstxt.org spec): H1 ad + > özet + giriş paragrafı + ## link
    listeleri + ## Optional. Küratörlü (envanter değil), sanitize'li (B-7).
    B-1: yalnız gerçekten kapsanan top_topics; opportunities dosyaya girmez
    (sitenin kapsamadığı konu = yanlış beyan), ayrı içerik-önerisi mesajına gider."""
    result = (audit or {}).get("result_json") or {}
    brand = result.get("brand_recall") or {}
    name = _sanitize_text(brand.get("inferred_name") or "", 60)
    if not name or len(name.split()) > 5:  # AI tahmini cümle-görünümlü/boş → domain
        name = domain
    topic = _sanitize_text(brand.get("inferred_topic") or "", 200)
    topics = [_sanitize_text(x.get("topic") if isinstance(x, dict) else x, 60)
              for x in (result.get("top_topics") or [])]
    topics = [t for t in dict.fromkeys(t for t in topics if t)][:8]
    pages = _curate_pages(result.get("pages") or [], domain)

    lines = [f"# {name}", ""]
    if topic:
        lines += [f"> {topic}", ""]
    if topics:
        lines += [
            f"{name} içerik odağı: {', '.join(topics)}. Bu dosya, yapay zekâ "
            "asistanlarının site içeriğini doğru anlaması için hazırlanmış bir "
            "içindekiler rehberidir.", ""]
    important, optional = pages[:12], pages[12:20]
    if important:
        lines += ["## Önemli Sayfalar", ""]
        for p in important:
            desc = f": {p['meta']}" if p["meta"] else ""
            lines.append(f"- [{p['title']}]({p['url']}){desc}")
        lines.append("")
    if optional:
        lines += ["## Optional", ""]
        for p in optional:
            lines.append(f"- [{p['title']}]({p['url']})")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def generate_schema_html(domain: str, audit: dict | None) -> str:
    """schema.org JSON-LD (Organization + WebSite) uretir - her sitede gecerli
    olan, guvenli bir taban yapisal veri. Marka adi/konusu GEONI taramasindan
    gelir, yoksa domain'e duser. Uzman/admin teslim oncesi genisletebilir."""
    result = (audit or {}).get("result_json") or {}
    brand = result.get("brand_recall") or {}
    assets = result.get("site_assets") or {}   # P1: crawler'dan logo/sameAs (yoksa {})
    name = _sanitize_text(brand.get("inferred_name") or "", 60) or domain
    desc = _sanitize_text(brand.get("inferred_topic") or "", 250)
    url = f"https://{domain}"
    lang = (audit or {}).get("lang") or result.get("lang") or "tr"
    topics = [_sanitize_text(x.get("topic") if isinstance(x, dict) else x, 60)
              for x in (result.get("top_topics") or [])]
    topics = [t for t in dict.fromkeys(t for t in topics if t)][:8]

    # B-6: @graph + @id çapraz referans (AI entity çözümü @id bağlarıyla yapar).
    # Asla uydurma — elde olmayan alan (logo/sameAs) atlanır.
    org = {"@type": "Organization", "@id": f"{url}/#organization", "name": name, "url": url}
    if desc:
        org["description"] = desc
    logo = assets.get("logo")
    if logo and _same_site_url(logo, domain):
        org["logo"] = {"@type": "ImageObject", "url": logo}
    same_as = [s for s in (assets.get("sameAs") or []) if isinstance(s, str) and s.startswith("http")]
    if same_as:
        org["sameAs"] = same_as[:8]
    if topics:
        org["knowsAbout"] = topics  # gerçek uzmanlık alanları (opportunities ASLA)

    website = {"@type": "WebSite", "@id": f"{url}/#website", "url": url, "name": name,
               "inLanguage": lang, "publisher": {"@id": f"{url}/#organization"}}

    graph = {"@context": "https://schema.org", "@graph": [org, website]}
    return ('<script type="application/ld+json">\n'
            + json.dumps(graph, ensure_ascii=False, indent=2)
            + "\n</script>")


async def fulfill_schema_ticket(ticket_id: int, domain: str) -> bool:
    """schema_setup icin otomatik teslim - llms_robots ile ayni desen: JSON-LD
    uretir, checklist'i isaretler, teslim mesaji + indirilebilir schema.html
    ekler, bileti 'submitted'a ceker (admin onayi hala gerekli)."""
    try:
        audit = await get_latest_web_audit_by_domain(domain)
        schema_html = generate_schema_html(domain, audit)

        tasks = await list_ticket_tasks(ticket_id)
        for task in tasks:  # B-5: yalniz gercekten yapilan (uretim) gorevleri isaretle
            await toggle_ticket_task(task["id"], ticket_id, _is_auto_completable(task.get("title", "")))

        ticket_type = await get_ticket_type_by_key("schema_setup")
        template = (ticket_type or {}).get("delivery_template") or ""
        message = (
            template.replace("{target}", domain).replace("{schema_code}", schema_html)
        ) if template else schema_html
        if not audit:
            message += (
                "\n\n> Not: Bu alan adı için henüz bir GEONI taraması bulunmadığından şema "
                "temel bilgilerle (Organization + WebSite) oluşturuldu. Bir web taraması "
                "yaptırıp buradan bize yazarsanız, marka adı ve konu bilgisiyle "
                "zenginleştirilmiş sürümünü bu bilete ücretsiz ekleriz."
            )

        await add_ticket_message(ticket_id, None, "system", body=message)

        schema_url = await upload_ticket_file(
            ticket_id, "schema.html", schema_html, content_type="text/html; charset=utf-8"
        )
        if schema_url:
            await add_ticket_message(ticket_id, None, "system",
                                     attachment_url=schema_url, attachment_name="schema.html")

        await mark_ticket_submitted(ticket_id)
        return True
    except Exception as e:
        logger.warning(f"fulfill_schema_ticket error: {e}")
        return False


# Insan-uzman gerektirmeyen, satin alinir alinmaz otomatik teslim edilen
# hizmet key'leri. Yeni bir otomatik hizmet eklerken buraya + dispatch'e ekle.
AUTO_FULFILL_KEYS = {"llms_robots", "schema_setup"}


async def fulfill_auto_ticket(key: str, ticket_id: int, target: str) -> bool:
    """Otomatik teslim dispatcher + SAVUNMA HATTI. Web-yüzeyi hizmetleri
    (llms_robots/schema) yalnız GEÇERLİ bir web sitesine uygulanır; target domain
    değilse (kişi/marka/sosyal ismi/@handle) ÇÖP dosya üretmek yerine müşteriden
    web sitesini ister ve bileti 'open' bırakır. (Sert kapı satın alma/INTENT'te;
    bu son savunma, oradan kaçan hiçbir hedefin çöp teslimat üretmemesini garanti eder.)"""
    website = normalize_domain(target)
    if website is None:
        await add_ticket_message(ticket_id, None, "system", body=(
            "Bu hizmet **web siteniz** için llms.txt / robots.txt / şema dosyaları "
            "üretir. Taramanız bir web adresi içermediğinden dosyalar henüz "
            "oluşturulamadı.\n\nWeb siteniz varsa **alan adınızı** (ör. `ornekmarka.com`) "
            "bu bilete yazın; dosyalarınızı oluşturup ekleyelim. Web siteniz yoksa "
            "bu hizmet uygulanamaz."
        ))
        logger.info(f"fulfill_auto_ticket: hedef domain degil ({target!r}), cop uretilmedi, bilet {ticket_id} open")
        return False
    if key == "llms_robots":
        return await fulfill_llms_robots_ticket(ticket_id, website)
    if key == "schema_setup":
        return await fulfill_schema_ticket(ticket_id, website)
    return False


async def fulfill_llms_robots_ticket(ticket_id: int, domain: str) -> bool:
    """purchase_ticket() bu bileti olusturduktan hemen sonra cagirir -
    uzman atama adimi tamamen atlanir. Basarisiz olursa bilet 'open'
    durumunda kalir, admin normal akista bir uzmana atayabilir (sessiz
    dusme - musteri parasini kaybetmez, sadece otomasyon devreye girmemis
    olur)."""
    try:
        audit = await get_latest_web_audit_by_domain(domain)
        sitemap_ok = await _sitemap_exists(domain, audit)
        robots_txt, robots_status = await generate_robots_txt(domain, sitemap_ok=sitemap_ok)
        llms_txt = generate_llms_txt(domain, audit)

        tasks = await list_ticket_tasks(ticket_id)
        for task in tasks:  # B-5: yalniz gercekten yapilan (uretim) gorevleri isaretle
            await toggle_ticket_task(task["id"], ticket_id, _is_auto_completable(task.get("title", "")))

        ticket_type = await get_ticket_type_by_key("llms_robots")
        template = (ticket_type or {}).get("delivery_template") or ""
        message = (
            template
            .replace("{target}", domain)
            .replace("{robots_txt}", robots_txt)
            .replace("{llms_txt}", llms_txt)
        ) if template else f"{robots_txt}\n\n---\n\n{llms_txt}"
        if not audit:
            # Tarama yoksa llms.txt yalnizca domain adiyla uretilir - zayif.
            # Musteriye durustce soyle ve zenginlestirme yolunu goster.
            message += (
                "\n\n> Not: Bu alan adı için henüz bir GEONI taraması bulunmadığından "
                "llms.txt temel bilgilerle oluşturuldu. Bir web taraması yaptırırsanız "
                "sayfa listesi ve konu özetleriyle zenginleştirilmiş sürümünü bu bilete "
                "ücretsiz ekleriz - taramadan sonra buradan mesaj yazmanız yeterli."
            )

        # B-2: robots merge durumuna göre dürüst not.
        if robots_status == "already_open":
            message += ("\n\n> ✅ Robots kontrolü: robots.txt dosyanız zaten tüm AI botlarına "
                        "açık — değişiklik gerekmedi. llms.txt ve şema ile AI görünürlük "
                        "altyapınızı tamamlıyoruz.")
        elif robots_status == "created":
            message += ("\n\n> ⚠️ Canlı robots.txt'inize ulaşılamadı. Sitenizde ZATEN robots.txt "
                        "VARSA yukarıdaki dosyayla DEĞİŞTİRMEYİN; yalnızca `--- GEONI` bölümünü "
                        "mevcut dosyanızın SONUNA ekleyin.")
        else:  # merged
            message += ("\n\n> Robots: mevcut robots.txt kurallarınız korunarak AI botlarına "
                        "erişim bloğu eklendi; dosyayı olduğu gibi kök dizininize koyabilirsiniz.")
        # B-4: sitemap kanıtı yoksa dürüst not (satır dosyaya yazılmadı).
        if not sitemap_ok:
            message += ("\n\n> Not: sitemap.xml bulunamadı. Oluşturursanız hem Google hem AI "
                        "botlar sayfalarınızı daha hızlı keşfeder — oluşturunca robots.txt'inize "
                        "`Sitemap:` satırını ekleyin.")

        await add_ticket_message(ticket_id, None, "system", body=message)

        # Uretilen dosyalari indirilebilir/paylasilabilir ek olarak da ekle -
        # musteri metni kopyalamak yerine dosyayi dogrudan siteyi yapan kisiye
        # yollayabilir. Yukleme basarisiz olursa metin zaten mesajda mevcut.
        robots_url = await upload_ticket_file(ticket_id, "robots.txt", robots_txt)
        if robots_url:
            await add_ticket_message(ticket_id, None, "system",
                                     attachment_url=robots_url, attachment_name="robots.txt")
        llms_url = await upload_ticket_file(ticket_id, "llms.txt", llms_txt)
        if llms_url:
            await add_ticket_message(ticket_id, None, "system",
                                     attachment_url=llms_url, attachment_name="llms.txt")

        # B-1 devamı: opportunities (sitenin KAPSAMADIĞI fırsat konuları) llms.txt'e
        # YAZILMAZ (yanlış beyan) ama müşteriye içerik önerisi olarak değerlidir.
        opps = [_sanitize_text(o.get("topic") if isinstance(o, dict) else o, 60)
                for o in ((audit or {}).get("result_json") or {}).get("opportunities", [])]
        opps = [o for o in dict.fromkeys(o for o in opps if o)][:6]
        if opps:
            await add_ticket_message(ticket_id, None, "system", body=(
                "💡 İçerik önerisi: Taramanızda, sitenizin **henüz kapsamadığı** ama AI "
                "asistanlarının bu alanda sık atıf yaptığı şu konular öne çıktı:\n"
                + "\n".join(f"- {o}" for o in opps)
                + "\n\nBu konularda içerik yayınlarsanız llms.txt'inize ücretsiz ekleriz — "
                "yayınlayınca bu bilete yazmanız yeterli. (Bu konular llms.txt'e yazılmadı; "
                "orada yalnızca sitenizin gerçekten kapsadığı konular yer alır.)"
            ))

        await mark_ticket_submitted(ticket_id)
        return True
    except Exception as e:
        logger.warning(f"fulfill_llms_robots_ticket error: {e}")
        return False
