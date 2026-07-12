"""
"AI Botlarına Erişim İzni" (llms_robots) bileti - tek 5 hizmet arasında
insan uzman gerektirmeyecek kadar basit: robots.txt + llms.txt icerigi
domain bilgisinden (ve varsa GEONI'nin kendi taramasindan) deterministik
olarak uretilebilir. Diger 4 hizmet (sema/wikidata/icerik/atif) gercek
arastirma/muhakeme gerektirdigi icin insan uzmanda kalir.

Otomasyon, uretilen dosyalari SYSTEM yazari olarak konusma akisina
ekleyip bileti 'submitted' durumuna cekiyor - musteriye ulasmadan once
hala bir INSAN admin onayi (admin_verify_ticket) gerekiyor, tamamen
gozetimsiz degil.
"""
import json
import logging

from indexing import TRAINING_CRAWLER_AGENTS, SEARCH_CRAWLER_AGENTS
from db import (
    get_latest_web_audit_by_domain, list_ticket_tasks, toggle_ticket_task,
    add_ticket_message, get_ticket_type_by_key, mark_ticket_submitted,
    upload_ticket_file,
)

logger = logging.getLogger(__name__)


def generate_robots_txt(domain: str) -> str:
    lines = []
    for agent in {**TRAINING_CRAWLER_AGENTS, **SEARCH_CRAWLER_AGENTS}.values():
        lines.append(f"User-agent: {agent}")
        lines.append("Allow: /")
        lines.append("")
    lines.append("User-agent: *")
    lines.append("Allow: /")
    lines.append("")
    lines.append(f"Sitemap: https://{domain}/sitemap.xml")
    return "\n".join(lines)


def generate_llms_txt(domain: str, audit: dict | None) -> str:
    result = (audit or {}).get("result_json") or {}
    brand = result.get("brand_recall", {})
    name = brand.get("inferred_name") or domain
    topic = brand.get("inferred_topic") or ""
    pages = result.get("pages") or []
    topics = (result.get("top_topics") or []) + (result.get("opportunities") or [])

    lines = [f"# {name}"]
    if topic:
        lines.append(f"> {topic}")
    lines.append("")
    if pages:
        lines.append("## Önemli Sayfalar")
        for p in pages[:15]:
            if not isinstance(p, dict):
                continue
            url = p.get("url")
            if not url:
                continue
            desc = f": {p['meta_description']}" if p.get("meta_description") else ""
            lines.append(f"- [{p.get('title') or url}]({url}){desc}")
        lines.append("")
    if topics:
        # top_topics/opportunities STRING DEGIL {topic,...} nesnesi olabilir -
        # dict hashlenemedigi icin once konu adini cikar, sonra tekrarsizlastir.
        names = [(x.get("topic") if isinstance(x, dict) else x) for x in topics]
        names = [n for n in dict.fromkeys(n for n in names if n)]
        if names:
            lines.append("## Öne Çıkan Konular")
            for n in names:
                lines.append(f"- {n}")
    return "\n".join(lines)


def generate_schema_html(domain: str, audit: dict | None) -> str:
    """schema.org JSON-LD (Organization + WebSite) uretir - her sitede gecerli
    olan, guvenli bir taban yapisal veri. Marka adi/konusu GEONI taramasindan
    gelir, yoksa domain'e duser. Uzman/admin teslim oncesi genisletebilir."""
    result = (audit or {}).get("result_json") or {}
    brand = result.get("brand_recall") or {}
    name = brand.get("inferred_name") or domain
    desc = brand.get("inferred_topic") or ""
    url = f"https://{domain}"

    org = {"@context": "https://schema.org", "@type": "Organization", "name": name, "url": url}
    if desc:
        org["description"] = desc
    website = {"@context": "https://schema.org", "@type": "WebSite", "name": name, "url": url}

    blocks = []
    for obj in (org, website):
        blocks.append(
            '<script type="application/ld+json">\n'
            + json.dumps(obj, ensure_ascii=False, indent=2)
            + "\n</script>"
        )
    return "\n".join(blocks)


async def fulfill_schema_ticket(ticket_id: int, domain: str) -> bool:
    """schema_setup icin otomatik teslim - llms_robots ile ayni desen: JSON-LD
    uretir, checklist'i isaretler, teslim mesaji + indirilebilir schema.html
    ekler, bileti 'submitted'a ceker (admin onayi hala gerekli)."""
    try:
        audit = await get_latest_web_audit_by_domain(domain)
        schema_html = generate_schema_html(domain, audit)

        tasks = await list_ticket_tasks(ticket_id)
        for task in tasks:
            await toggle_ticket_task(task["id"], ticket_id, True)

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


async def fulfill_auto_ticket(key: str, ticket_id: int, domain: str) -> bool:
    """ticket_type_key'e gore dogru otomatik teslim fonksiyonuna yonlendirir."""
    if key == "llms_robots":
        return await fulfill_llms_robots_ticket(ticket_id, domain)
    if key == "schema_setup":
        return await fulfill_schema_ticket(ticket_id, domain)
    return False


async def fulfill_llms_robots_ticket(ticket_id: int, domain: str) -> bool:
    """purchase_ticket() bu bileti olusturduktan hemen sonra cagirir -
    uzman atama adimi tamamen atlanir. Basarisiz olursa bilet 'open'
    durumunda kalir, admin normal akista bir uzmana atayabilir (sessiz
    dusme - musteri parasini kaybetmez, sadece otomasyon devreye girmemis
    olur)."""
    try:
        audit = await get_latest_web_audit_by_domain(domain)
        robots_txt = generate_robots_txt(domain)
        llms_txt = generate_llms_txt(domain, audit)

        tasks = await list_ticket_tasks(ticket_id)
        for task in tasks:
            await toggle_ticket_task(task["id"], ticket_id, True)

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

        await mark_ticket_submitted(ticket_id)
        return True
    except Exception as e:
        logger.warning(f"fulfill_llms_robots_ticket error: {e}")
        return False
