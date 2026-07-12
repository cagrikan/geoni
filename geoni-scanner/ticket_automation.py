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
import logging

from indexing import TRAINING_CRAWLER_AGENTS, SEARCH_CRAWLER_AGENTS
from db import (
    get_latest_web_audit_by_domain, list_ticket_tasks, toggle_ticket_task,
    add_ticket_message, get_ticket_type_by_key, mark_ticket_submitted,
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
        await mark_ticket_submitted(ticket_id)
        return True
    except Exception as e:
        logger.warning(f"fulfill_llms_robots_ticket error: {e}")
        return False
