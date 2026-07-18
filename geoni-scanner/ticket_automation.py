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
import os
import re
import unicodedata
from datetime import date
from urllib.robotparser import RobotFileParser

import httpx

from ssrf_guard import safe_get
from indexing import TRAINING_CRAWLER_AGENTS, SEARCH_CRAWLER_AGENTS
from db import (
    get_latest_web_audit_by_domain, get_latest_audit_by_target,
    list_ticket_tasks, toggle_ticket_task,
    add_ticket_message, get_ticket_type_by_key, mark_ticket_submitted,
    upload_ticket_file, normalize_domain, DOMAIN_ONLY_SERVICE_KEYS,
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
        # düşük bug: URL markdown link parantezine giriyor — boşluk/parantez/kontrol
        # karakteri içeren URL bağlantıyı kırar/enjekte eder, ele.
        if re.search(r"[\s()<>\[\]`]", url):
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


async def _find_sitemap(domain: str, audit: dict | None) -> str | None:
    """B-4: Sitemap: satiri yalniz KANITLA yazilir. sitemap.xml VE sitemap_index.xml
    kontrol edilir; BULUNAN relatif path doner (yoksa None → satir yazilmaz).
    audit sitemap_found=False diyorsa canli kontrole gerek yok (crawl'a guven)."""
    if audit and (audit.get("result_json") or {}).get("sitemap_found") is False:
        return None
    for path in ("sitemap.xml", "sitemap_index.xml"):
        try:
            async with httpx.AsyncClient() as c:
                r = await safe_get(c, f"https://{domain}/{path}", timeout=8, headers=_UA)
            body = (r.text or "")[:2000].lower()
            if r.status_code == 200 and ("<urlset" in body or "<sitemapindex" in body or "<loc>" in body):
                return path
        except Exception:
            continue
    return None


def _extract_star_disallows(robots_text: str) -> list[str]:
    """Mevcut robots.txt'te 'User-agent: *' grubunun Disallow satirlarini cikarir;
    GEONI blogu bu botlari '*' grubundan cikardigi icin hassas path'ler bloga
    kopyalanir (musterinin wp-admin/staging korumasi AI botlarda da korunur)."""
    # BUG-3: ardışık User-agent satırları TEK grup sayılır (spec). "User-agent: *"
    # + "User-agent: Googlebot" + "Disallow: /x" → /x hem *'a hem Googlebot'a uygulanır.
    # Bir grup: art arda UA satırları + kurallar; kural sonrası UA yeni grup başlatır.
    dis, group_has_star, prev_was_rule = [], False, False
    for raw in (robots_text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("user-agent:"):
            if prev_was_rule:  # önceki satır kuraldıysa yeni grup başlıyor
                group_has_star = False
            prev_was_rule = False
            if line.split(":", 1)[1].strip() == "*":
                group_has_star = True
        elif low.startswith(("disallow:", "allow:", "crawl-delay:", "sitemap:")):
            prev_was_rule = True
            if group_has_star and low.startswith("disallow:"):
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


async def generate_robots_txt(domain: str, sitemap_path: str | None = None) -> tuple[str, str, list]:
    """B-2: robots.txt'i ASLA korlemesine uretme — canli dosyayi cek, mevcut
    kurallari KORU, AI botlarina erisim blogunu EKLE. Doner: (metin, durum, kisitli_botlar).
    durum: 'created' (mevcut cekilemedi) | 'already_open' (tum AI botlari gercekten
    derin-path'lere erisebiliyor) | 'merged' (bazi botlara blok eklendi) | 'needs_manual'
    (kisitli botlarin HEPSININ kendi grubu var, ekleme gecersiz kilamaz -> musteri
    elle duzeltmeli). kisitli_botlar: kendi grubunda kisitli olanlar."""
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

    def _build_block(agents: list[str], disallows: list[str]) -> str:
        bl = [f"# --- GEONI: AI botlarina erisim izni (eklendi: {date.today().isoformat()}) ---",
              "# Bu bloklar yapay zeka arama/alintilama botlarina site erisimi verir."]
        bl += [f"User-agent: {a}" for a in agents]
        bl += [f"Disallow: {d}" for d in disallows]  # hassas path'leri koru
        bl.append("Allow: /")
        return "\n".join(bl)

    if existing is None:
        header = (
            "# robots.txt — GEONI tarafindan olusturuldu.\n"
            "# DIKKAT: Sitenizde ZATEN robots.txt VARSA bu dosyayla DEGISTIRMEYIN;\n"
            "# yalnizca asagidaki '--- GEONI' bolumunu mevcut dosyanizin SONUNA ekleyin.\n"
        )
        out = header + "\nUser-agent: *\nAllow: /\n\n" + _build_block(_AI_AGENTS, [])
        if sitemap_path:
            out += f"\n\nSitemap: https://{domain}/{sitemap_path}"
        return out + "\n", "created", []

    # BUG-1/BUG-2 düzeltmesi: her bot için durumu DOĞRU sınıflandır.
    # - 'open'       : temsili derin path'lerin HEPSİNE erişebiliyor (gerçekten açık)
    # - 'appendable' : kendi grubu YOK (yalnız '*'a tabi) → bot-özel grup eklemek
    #                  '*'ı geçersiz kılar (Python RobotFileParser specific>default)
    # - 'restricted' : kendi adına grubu VAR ve kısıtlı → EKLEME bunu geçersiz
    #                  KILAMAZ (ilk-eşleşme; prod Python 3.10) → müşteri elle düzeltmeli
    rp = RobotFileParser()
    rp.parse(existing.splitlines())
    probe = ["/", "/blog/", "/blog/ornek", "/products/", "/urunler/", "/hizmetler/",
             "/services/", "/about", "/hakkimizda"]
    appendable, restricted = [], []
    for agent in _AI_AGENTS:
        fully = all(rp.can_fetch(agent, p) for p in probe)
        if fully:
            continue
        has_own = re.search(rf"(?im)^\s*user-agent:\s*{re.escape(agent)}\s*(?:#.*)?$", existing)
        (restricted if has_own else appendable).append(agent)

    if not appendable and not restricted:
        return existing.rstrip() + "\n", "already_open", []

    out = existing.rstrip()
    if appendable:
        out += "\n\n" + _build_block(appendable, _extract_star_disallows(existing))
        if sitemap_path and "sitemap:" not in existing.lower():
            out += f"\n\nSitemap: https://{domain}/{sitemap_path}"
    status = "merged" if appendable else "needs_manual"
    return out + "\n", status, restricted


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


def generate_schema_html(domain: str, audit: dict | None,
                         wikidata_qid: str | None = None) -> str:
    """schema.org JSON-LD (Organization + WebSite) uretir - her sitede gecerli
    olan, guvenli bir taban yapisal veri. Marka adi/konusu GEONI taramasindan
    gelir, yoksa domain'e duser. Uzman/admin teslim oncesi genisletebilir.

    M2 (entity zinciri): wikidata_qid parametrede VEYA audit'te (result.wikidata_qid)
    varsa Organization `sameAs`'ine https://www.wikidata.org/wiki/{QID} eklenir —
    böylece site (`@id`) ↔ Wikidata çift-yönlü bağ kurulur (AI entity çözümü bu
    @id/sameAs üçgeniyle yapar). QID yoksa davranış AYNEN eskisi gibi."""
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
    # BUG-6: logo çoğu sitede CDN'den (cdn.*, cloudfront, imgix...) servis edilir;
    # _same_site_url şartı bunları düşürüyordu. https bir URL yeterli (crawler zaten
    # ana sayfanın og:image/icon'undan aldı — kaynak güvenilir).
    logo = assets.get("logo")
    if isinstance(logo, str) and logo.startswith("https://"):
        org["logo"] = {"@type": "ImageObject", "url": logo}
    same_as = [s for s in (assets.get("sameAs") or [])
               if isinstance(s, str) and s.startswith(("https://", "http://"))]
    # M2: Wikidata QID köprüsü — sameAs listesinin BAŞINA eklenir (entity çapası).
    qid = wikidata_qid or (result.get("wikidata_qid")
                           if isinstance(result.get("wikidata_qid"), str) else None)
    if qid and re.fullmatch(r"Q\d+", qid):
        wd_url = f"https://www.wikidata.org/wiki/{qid}"
        same_as = [wd_url] + [s for s in same_as if s != wd_url]
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


# ── content_package otomasyonu (2.3) ───────────────────────────────────────
# Konu SEÇİMİ deterministik (taramadan: kanıtlanmış boşluklar), METİN üretimi LLM.
# Halüsinasyon alanı dar tutulur: konu/sorgu/rakip adları hep gerçek veriden.

_LLM_TIMEOUT = 45


async def _llm_text(prompt: str, max_tokens: int = 1800) -> str | None:
    """topics.py PROVIDER_CHAIN deseninin METİN (JSON değil) döndüren hali:
    Anthropic → OpenAI → Gemini, ilk başarılı yanıtın düz metnini döner. Hiçbiri
    yoksa/hepsi düşerse None (çağıran insana düşürür). Anahtarlar env'den."""
    ak, ok, gk = (os.environ.get("ANTHROPIC_API_KEY", ""),
                  os.environ.get("OPENAI_API_KEY", ""),
                  os.environ.get("GOOGLE_API_KEY", ""))
    try:
        if ak:
            async with httpx.AsyncClient() as c:
                r = await c.post("https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": ak, "anthropic-version": "2023-06-01",
                             "content-type": "application/json"},
                    json={"model": "claude-sonnet-4-6", "max_tokens": max_tokens,
                          "messages": [{"role": "user", "content": prompt}]},
                    timeout=_LLM_TIMEOUT)
            if r.status_code == 200:
                txt = "".join(b.get("text", "") for b in r.json().get("content", [])
                              if b.get("type") == "text").strip()
                if txt:
                    return txt
            else:
                logger.warning(f"content LLM anthropic {r.status_code}: {r.text[:160]}")
    except Exception as e:
        logger.warning(f"content LLM anthropic failed: {e}")
    try:
        if ok:
            async with httpx.AsyncClient() as c:
                r = await c.post("https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {ok}", "Content-Type": "application/json"},
                    json={"model": "gpt-4o", "max_tokens": max_tokens,
                          "messages": [{"role": "user", "content": prompt}]},
                    timeout=_LLM_TIMEOUT)
            if r.status_code == 200:
                txt = (r.json()["choices"][0]["message"]["content"] or "").strip()
                if txt:
                    return txt
    except Exception as e:
        logger.warning(f"content LLM openai failed: {e}")
    try:
        if gk:
            async with httpx.AsyncClient() as c:
                r = await c.post(
                    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
                    headers={"x-goog-api-key": gk},
                    json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=_LLM_TIMEOUT)
            if r.status_code == 200:
                txt = (r.json()["candidates"][0]["content"]["parts"][0]["text"] or "").strip()
                if txt:
                    return txt
    except Exception as e:
        logger.warning(f"content LLM gemini failed: {e}")
    return None


def _select_content_topics(audit: dict | None) -> dict:
    """DETERMİNİSTİK konu seçimi (LLM YOK) — hepsi gerçek tarama verisinden:
    1) sov.queries mentioned=False & adjacent değil → KANITLANMIŞ boşluk (öncelik)
    2) opportunities → sitenin kapsamadığı, kategoride atıf alan konular
    3) top_topics → derinleştirme adayı
    4) sov.competitors → karşılaştırma içeriği ("X vs Y", "X alternatifleri")
    Döner: {gaps, opportunities, strengths, competitors, has_sov}."""
    result = (audit or {}).get("result_json") or {}
    sov = result.get("sov") or {}
    gaps = []
    for q in (sov.get("queries") or []):
        if not q.get("mentioned") and not q.get("adjacent"):
            qt = _sanitize_text(q.get("query") or "", 120)
            if qt:
                gaps.append(qt)
    opps = [_sanitize_text(o.get("topic") if isinstance(o, dict) else o, 80)
            for o in (result.get("opportunities") or [])]
    opps = [o for o in dict.fromkeys(o for o in opps if o)]
    strengths = [_sanitize_text(t.get("topic") if isinstance(t, dict) else t, 80)
                 for t in (result.get("top_topics") or [])]
    strengths = [s for s in dict.fromkeys(s for s in strengths if s)]
    comps = [_sanitize_text(c.get("name") if isinstance(c, dict) else c, 60)
             for c in (sov.get("competitors") or [])]
    comps = [c for c in dict.fromkeys(c for c in comps if c)]
    return {"gaps": gaps[:6], "opportunities": opps[:6], "strengths": strengths[:6],
            "competitors": comps[:6], "has_sov": bool(sov.get("checked"))}


def _content_has_material(sel: dict) -> bool:
    """Otonom üretim için yeterli deterministik hammadde var mı? Yoksa insana
    düşülür (çöp/jenerik içerik üretmektense uzman doğru veriden yazsın)."""
    return bool(sel["gaps"] or sel["opportunities"] or sel["strengths"] or sel["competitors"])


async def generate_content_briefs(name: str, topic: str, audit: dict | None, kind: str) -> str | None:
    """3 içerik brief'i üretir. Konular deterministik (yukarıda seçildi), metni LLM
    yazar (AEO/GEO kurallarıyla). kind='web' → makale brief; aksi (person/brand/
    social) → bio/platform-metni modu. Yeterli materyal yoksa None."""
    sel = _select_content_topics(audit)
    if not _content_has_material(sel):
        return None
    data_lines = []
    if sel["gaps"]:
        data_lines.append("KANITLANMIŞ BOŞLUKLAR (AI'a soruldu, marka anılmadı — en öncelikli hedefler): "
                          + "; ".join(sel["gaps"]))
    if sel["opportunities"]:
        data_lines.append("FIRSAT KONULARI (kategoride atıf alan, sitenin kapsamadığı): "
                          + "; ".join(sel["opportunities"]))
    if sel["strengths"]:
        data_lines.append("GÜÇLÜ KONULAR (derinleştirme adayı): " + "; ".join(sel["strengths"]))
    if sel["competitors"]:
        data_lines.append("RAKİPLER (karşılaştırma içeriği için): " + "; ".join(sel["competitors"]))
    data_block = "\n".join(f"- {l}" for l in data_lines)

    if kind == "web":
        fmt = (
            "Her brief AYNEN şu markdown formatında olsun:\n"
            "## Brief N — HEDEF: \"<hedef sorgu>\" (<neden: taramada boşluk/fırsat>)\n"
            "**Başlık:** <SEO+AEO uyumlu, merak uyandıran başlık>\n"
            "**Hedef sorgu(lar):** <2-3 gerçek arama sorgusu>\n"
            "**İlk paragraf cevabı:** <ilk 2 cümlede verilecek net cevabın ne olacağı>\n"
            "**Ana sorular (H2):** <4-5 soru-başlık, • ile ayır>\n"
            "**Özgün değer:** <başka yerde olmayan öğe: fiyat aralığı/vaka/yerel veri/mini-anket>\n"
            "**SSS (FAQPage schema):** <kaç soru> · **Uzunluk:** <800-1500 kelime> · **Dil:** <dil>\n"
            "**Yayın:** <kendi blog → LinkedIn/Medium özeti> · **Yayınlanınca:** llms.txt güncellemesi için bilete URL yazın.\n"
        )
        role = "AEO (answer engine optimization) uzmanı bir içerik stratejistisin"
    else:
        fmt = (
            "Web sitesi olmayan bir KİŞİ/MARKA için 'içerik paketi' brief'leri. Her biri:\n"
            "## Brief N — HEDEF: \"<konu/sorgu>\"\n"
            "**Platform:** <YouTube video / LinkedIn yazı / X başlık vb.>\n"
            "**Başlık/konu:** <kişinin adının bu konuyla yan yana geçeceği metin>\n"
            "**İlk cümle cevabı:** <net, alıntılanabilir açılış>\n"
            "**Ana noktalar:** <3-4 madde, • ile>\n"
            "**Bio tutarlılığı notu:** <tüm platform bio'larında geçmesi gereken kimlik cümlesi>\n"
            "**Özgün değer:** <kişiye özel deneyim/veri>\n"
        )
        role = "kişisel marka + AI görünürlüğü uzmanısın"
    prompt = (
        f"Sen {role}. Amaç: yapay zekâ arama motorlarının (ChatGPT, Perplexity, "
        f"Gemini, Claude) ALINTILAYACAĞI içerik ürettirmek.\n\n"
        f"MARKA/KİŞİ: {name}\nKONU/ALAN: {topic or '(taramadan)'}\n\n"
        f"AŞAĞIDAKİ GERÇEK TARAMA VERİSİNDEN 3 içerik brief'i üret (konuları BU "
        f"veriden seç, uydurma):\n{data_block}\n\n"
        f"AEO/GEO kuralları: soru-başlık + ilk 2 cümlede net cevap; her H2 bağımsız "
        f"alıntılanabilir; en az bir özgün veri/istatistik; FAQPage için SSS; gerçek "
        f"yazar+tarih (E-E-A-T). Şişirme yok.\n\n{fmt}\n"
        f"Yalnız 3 brief'i markdown olarak ver, başka açıklama ekleme."
    )
    return await _llm_text(prompt, max_tokens=1800)


async def generate_content_draft(brief_block: str, name: str, lang: str) -> str | None:
    """İlk brief'in TAM yazı taslağını üretir. 'TASLAK' etiketli, müşterinin kendi
    verisini (fiyat/vaka) ekleyeceği [KÖŞELİ PARANTEZ] boşluklu — sahte 'insan
    yazdı' izlenimi vermeden (C-1 dersi)."""
    lang_name = "İngilizce" if (lang or "tr").startswith("en") else "Türkçe"
    prompt = (
        f"Aşağıdaki içerik brief'lerinin İLK brief'i için TAM bir makale taslağı yaz "
        f"({lang_name}). AEO kuralları: H1 başlık; ilk paragrafta 40-60 kelimede net "
        f"cevap; soru formunda H2'ler; her bölüm bağımsız alıntılanabilir; sonda 5 "
        f"soruluk SSS. Marka: {name}.\n\n"
        f"ÖNEMLİ: Müşterinin kendi verisini eklemesi gereken yerlere [KÖŞELİ PARANTEZ] "
        f"içinde açık talimat bırak (ör. [BURAYA kliniğinizin 2026 fiyat aralığını "
        f"yazın — AI fiyat veren kaynağı alıntılar]). Uydurma sayı/iddia YAZMA; "
        f"doğrulanması gereken yerleri parantezle işaretle.\n\n"
        f"BRIEF'LER:\n{brief_block[:3000]}\n\n"
        f"Yalnız makale taslağını markdown ver."
    )
    return await _llm_text(prompt, max_tokens=2200)


async def fulfill_content_ticket(ticket_id: int, target: str) -> bool:
    """content_package otonom teslimi: 3 brief + 1 taslak + yayın planı → bilete
    dosya+mesaj olarak eklenir, 'submitted'a çekilir. Yeterli tarama verisi yoksa
    (sov/opportunities boş) insana düşer (return False → dispatch notify_experts)."""
    try:
        audit = await get_latest_audit_by_target(target)
        sel = _select_content_topics(audit)
        if not _content_has_material(sel):
            await add_ticket_message(ticket_id, None, "system", body=(
                "İçerik paketiniz için önce bir GEONI taraması gerekiyor: içerik "
                "konularını **tahmin değil**, taramanızın bulduğu gerçek boşluklardan "
                "(AI'ın sizi anmadığı sorgular, kategori fırsatları) seçiyoruz. Bu "
                "hedef için tamamlanmış tarama bulunamadı.\n\nBir tarama yaptırıp bu "
                "bilete yazarsanız içerik paketinizi hemen oluşturup ekleriz."
            ))
            logger.info(f"fulfill_content_ticket: yetersiz materyal (t={ticket_id}), insana dusuldu")
            return False

        result = (audit or {}).get("result_json") or {}
        atype = (audit or {}).get("type") or "web"
        kind = "web" if atype == "web" else "social"
        brand = result.get("brand_recall") or {}
        name = _sanitize_text(brand.get("inferred_name") or "", 60) or (
            normalize_domain(target) or target)
        topic = _sanitize_text(brand.get("inferred_topic") or "", 200)
        lang = (audit or {}).get("lang") or result.get("lang") or "tr"

        briefs = await generate_content_briefs(name, topic, audit, kind)
        briefs = _lint_delivery_message(briefs or "")
        if not briefs:
            logger.info(f"fulfill_content_ticket: brief uretimi dustu (t={ticket_id}), insana dusuldu")
            return False
        draft = await generate_content_draft(briefs, name, lang)
        draft = (draft or "").strip() or None

        tasks = await list_ticket_tasks(ticket_id)
        for task in tasks:  # B-5: yalnız üretim görevleri; "yayınla/onayla" işaretlenmez
            await toggle_ticket_task(task["id"], ticket_id, _is_auto_completable(task.get("title", "")))

        gap_note = ""
        if sel["gaps"]:
            gap_note = ("\n\nBu brief'ler **taramanızda AI'ın sizi anmadığı** şu sorgulardan "
                        "seçildi (kanıtlanmış boşluklar): " + ", ".join(f"“{g}”" for g in sel["gaps"][:4]) + ".")
        message = (
            f"# İçerik Paketiniz hazır\n\n{name} için yapay zekâ motorlarının "
            f"**alıntılayacağı** içerik planını oluşturduk: 3 içerik brief'i + 1 tam "
            f"yazı taslağı + yayın planı.{gap_note}\n\n"
            f"---\n\n{briefs}\n"
        )
        if draft:
            message += (
                "\n---\n\n## İlk içeriğin tam taslağı (TASLAK)\n\n"
                "> Bu taslak yayına hazır bir iskelettir. **[köşeli parantez]** içindeki "
                "yerlere kendi verilerinizi (fiyat, vaka, yerel bilgi) ekleyin — AI "
                "motorları özgün veri veren kaynağı alıntılar. Yayınlamadan önce "
                "uzmanlık doğrulaması yapın.\n\n" + draft + "\n"
            )
        message += (
            "\n---\n\n**Yayınlayınca:** içerik URL'sini bu bilete yazın; sayfayı "
            "llms.txt'inize ücretsiz ekler, güncelliğini izlemenize yardımcı oluruz. "
            "İçeriği bir sektör sitesine **misafir yazı** olarak da verebilirsiniz — "
            "üçüncü-taraf atıf için “Güvenilir Kaynaklarda Görünürlük” hizmetimiz tam bunu yapar."
        )
        message = _lint_delivery_message(message)
        if not message:
            return False
        await add_ticket_message(ticket_id, None, "system", body=message)

        briefs_url = await upload_ticket_file(ticket_id, "icerik-briefleri.md", briefs)
        if briefs_url:
            await add_ticket_message(ticket_id, None, "system",
                                     attachment_url=briefs_url, attachment_name="icerik-briefleri.md")
        if draft:
            draft_url = await upload_ticket_file(ticket_id, "taslak-1.md", draft)
            if draft_url:
                await add_ticket_message(ticket_id, None, "system",
                                         attachment_url=draft_url, attachment_name="taslak-1.md")

        await mark_ticket_submitted(ticket_id)
        return True
    except Exception as e:
        logger.warning(f"fulfill_content_ticket error: {e}")
        return False


# ── citation_placement yarı-otonom istihbarat katmanı (2.4) ─────────────────
# İstihbarat + malzeme (%40) otomatik; yerleştirme (üçüncü tarafın kararı) İNSAN.
# Hedef tablosu TAMAMEN deterministik (sov.sources/citation_gap/competitors),
# outreach şablonları LLM. Teslim 'submitted' YAPILMAZ — uzman bitirir.

def _query_domain_map(sov: dict) -> dict:
    """Hangi domain hangi sorgularda atıf aldı → {domain: [query,...]}.
    sov.queries[].engines[].sources'tan deterministik türetilir."""
    m: dict[str, set] = {}
    for q in (sov.get("queries") or []):
        qt = _sanitize_text(q.get("query") or "", 80)
        if not qt:
            continue
        for eng in (q.get("engines") or {}).values():
            for d in (eng.get("sources") or []):
                m.setdefault(d, set()).add(qt)
    return {d: sorted(qs) for d, qs in m.items()}


def generate_citation_targets(audit: dict | None) -> str | None:
    """DETERMİNİSTİK 'Atıf Hedef Listesi' (LLM YOK) — hepsi gerçek taramadan:
    citation_gap (rakip anan/marka yok) + sov.sources(own=False) birincil hedef;
    her satıra hangi sorgularda atıf aldığı + rakip eşlemesi bağlanır. Yeterli
    kaynak yoksa None (insana düşer)."""
    result = (audit or {}).get("result_json") or {}
    sov = result.get("sov") or {}
    if not sov.get("checked"):
        return None
    qmap = _query_domain_map(sov)
    # Birincil: citation_gap (T3 — rakip anan, markayı anmayan). Sonra sov.sources own=False.
    seen, rows = set(), []
    for g in (sov.get("citation_gap") or []):
        d = (g.get("domain") or "").strip().lower()
        if d and d not in seen:
            seen.add(d)
            rows.append((d, int(g.get("mentions") or 0), qmap.get(d, []), True))
    for s in (sov.get("sources") or []):
        if s.get("own"):
            continue
        d = (s.get("domain") or "").strip().lower()
        if d and d not in seen:
            seen.add(d)
            rows.append((d, int(s.get("mentions") or 0), qmap.get(d, []), False))
    if not rows:
        return None
    rows.sort(key=lambda r: r[1], reverse=True)
    rows = rows[:12]

    comps = [(_sanitize_text(c.get("name") if isinstance(c, dict) else c, 60),
              int(c.get("mentions") or 0) if isinstance(c, dict) else 0)
             for c in (sov.get("competitors") or [])]
    comps = [(n, m) for n, m in comps if n]

    today = date.today().isoformat()
    out = [f"# Atıf Hedef Listesi (taramadan üretildi, {today})", "",
           "AI motorlarının bu kategoride **gerçekten atıf yaptığı** kaynaklar. "
           "`gap` = rakiplerinizin anıldığı ama sizin anılmadığınız kaynak (en öncelikli).", "",
           "| # | Kaynak | Neden hedef | Önerilen aksiyon | Öncelik |",
           "|---|--------|-------------|------------------|---------|"]
    for i, (d, m, qs, is_gap) in enumerate(rows, 1):
        why = ("rakip anıldı, siz yoksunuz" if is_gap else "AI bu kategoride atıf yaptı")
        if qs:
            why += f" ({len(qs)} sorguda: " + ", ".join(f"“{q}”" for q in qs[:2]) + ")"
        elif m:
            why += f" ({m} yanıtta)"
        action = "dizin kaydı / listicle'a ekleme talebi / misafir yazı (uzman belirler)"
        pri = "Yüksek" if is_gap or m >= 3 else "Orta"
        out.append(f"| {i} | {d} | {why} | {action} | {pri} |")
    if comps:
        out += ["", "## Rakip-kaynak eşlemesi",
                "AI'ın bu kategoride andığı rakipler (hangi içerikte geçtiklerini bulmak outreach hedefidir):", ""]
        for n, m in comps[:8]:
            out.append(f"- **{n}**" + (f" — {m} yanıtta anıldı" if m else ""))
    out += ["", "> Kırmızı çizgi: satın alınmış/spam link YOK. Anılma bağlam içinde "
            "(marka + ne yaptığı + kategori aynı cümlede). Her yerleşim URL + arşivle kanıtlanır.",
            "> Ölçüm vaadi: 4-8 hafta sonra tekrar tarama — `own_cited` ve kategori görünürlüğü artışını birlikte görürüz."]
    return "\n".join(out)


async def generate_outreach_templates(name: str, topic: str, lang: str) -> str | None:
    """Markaya/kategoriye özel 2-3 outreach şablonu (LLM). content ile aynı zincir."""
    lang_name = "İngilizce" if (lang or "tr").startswith("en") else "Türkçe"
    prompt = (
        f"Sen bir dijital PR / linkbuilding uzmanısın. '{name}' ({topic or 'kategori'}) "
        f"için yapay zekâ motorlarının alıntıladığı güvenilir kaynaklarda anılmayı "
        f"sağlayacak 2-3 kısa outreach ŞABLONU yaz ({lang_name}):\n"
        f"1) Listicle/derleme ekleme talebi (rakip zaten listede; markanın somut farkını 1 cümlede ver)\n"
        f"2) Sektör medyasına misafir yazı pitch'i (özgün açı: kategoride AI görünürlüğü verisi bile bir hikâye)\n"
        f"3) Uzman görüşü/röportaj teklifi\n\n"
        f"Her şablon: kısa, kişiselleştirilebilir [KÖŞELİ PARANTEZ] alanlı, spam DEĞİL, "
        f"editoryal değer vurgulu. Yalnız şablonları markdown ver."
    )
    return await _llm_text(prompt, max_tokens=1400)


async def prepare_citation_ticket(ticket_id: int, target: str) -> bool:
    """Yarı-otonom hazırlık: 'Atıf İstihbarat Raporu' (hedef tablo + şablonlar)
    bilete düşer, müşteri ANINDA değer görür. 'submitted' YAPILMAZ — outreach'i
    uzman yürütür (notify_experts'i dispatch çağırır, burada DEĞİL). Yeterli veri
    yoksa False (yine de uzman bilgilendirilecek; müşteriye dürüst not düşülür)."""
    try:
        audit = await get_latest_audit_by_target(target)
        targets = generate_citation_targets(audit)
        if not targets:
            await add_ticket_message(ticket_id, None, "system", body=(
                "Güvenilir kaynaklarda görünürlük hizmeti için hedef listesini "
                "taramanızın **ölçülmüş kaynak verisinden** (AI'ın kategoride kime "
                "atıf yaptığı) çıkarıyoruz. Bu hedef için bu veriyi taşıyan bir tarama "
                "bulunamadı.\n\nUzmanımız sizinle iletişime geçip hedefleri elle "
                "belirleyecek; dilerseniz güncel bir tarama yaptırın, listeyi hemen çıkaralım."
            ))
            logger.info(f"prepare_citation_ticket: sov verisi yok (t={ticket_id}), uzmana devir")
            return False

        result = (audit or {}).get("result_json") or {}
        brand = result.get("brand_recall") or {}
        name = _sanitize_text(brand.get("inferred_name") or "", 60) or (
            normalize_domain(target) or target)
        topic = _sanitize_text(brand.get("inferred_topic") or "", 200)
        lang = (audit or {}).get("lang") or result.get("lang") or "tr"
        templates = await generate_outreach_templates(name, topic, lang)

        msg = (
            f"# Atıf İstihbarat Raporunuz hazır\n\n{name} için yapay zekâ motorlarının "
            f"bu kategoride **gerçekten atıf yaptığı** kaynakların ölçülmüş listesini "
            f"çıkardık (aşağıda + dosya olarak). Uzmanımız bu hedeflere göre outreach'e "
            f"başlıyor; yerleşimleri kanıtlarıyla (URL + arşiv) size teslim edecek.\n\n"
            f"---\n\n{targets}\n"
        )
        msg = _lint_delivery_message(msg)
        if not msg:
            return False
        await add_ticket_message(ticket_id, None, "system", body=msg)

        targets_url = await upload_ticket_file(ticket_id, "atif-hedef-listesi.md", targets)
        if targets_url:
            await add_ticket_message(ticket_id, None, "system",
                                     attachment_url=targets_url, attachment_name="atif-hedef-listesi.md")
        if templates and templates.strip():
            tpl_url = await upload_ticket_file(ticket_id, "outreach-sablonlari.md", templates.strip())
            if tpl_url:
                await add_ticket_message(ticket_id, None, "system",
                                         attachment_url=tpl_url, attachment_name="outreach-sablonlari.md")
        # NOT: mark_ticket_submitted YOK — yerleştirme insan işi (yarı-otonom).
        return True
    except Exception as e:
        logger.warning(f"prepare_citation_ticket error: {e}")
        return False


# ── wikidata_entity yarı-otonom taslak (2.5) ────────────────────────────────
# M2 entity zinciri: markanın/kişinin AI'ların tanıdığı bir VARLIK olması.
# Otomasyon TASLAK üretir (QuickStatements + rapor + QID'li schema); YAYINLAMA
# İNSAN adımıdır — Wikidata bot politikası/COI/silme riski nedeniyle otomatik
# yazma YAPILMAZ (dokümanda net). citation_placement ile aynı yarı-otonom desen:
# 'submitted' YAPILMAZ, notify_experts'i dispatch çağırır.

# Sosyal sameAs URL'i → Wikidata property + tanımlayıcı. Yalnız TEMİZ eşleşmeler;
# belirsiz/gürültülü path'ler atlanır (çöp claim yazmaktansa insan ekler).
_WD_SOCIAL_PROPS = [
    ("P2013", re.compile(r"facebook\.com/([A-Za-z0-9.]+)", re.I)),                 # Facebook
    ("P2003", re.compile(r"instagram\.com/([A-Za-z0-9_.]+)", re.I)),               # Instagram
    ("P2002", re.compile(r"(?:twitter|x)\.com/@?([A-Za-z0-9_]+)", re.I)),          # X / Twitter
    ("P7085", re.compile(r"tiktok\.com/@?([A-Za-z0-9_.]+)", re.I)),                # TikTok
    ("P6634", re.compile(r"linkedin\.com/in/([A-Za-z0-9_%-]+)", re.I)),            # LinkedIn (kişi)
    ("P4264", re.compile(r"linkedin\.com/company/([A-Za-z0-9_%-]+)", re.I)),       # LinkedIn (kuruluş)
    ("P2397", re.compile(r"youtube\.com/(?:channel/|c/|user/|@)?([A-Za-z0-9_-]+)", re.I)),  # YouTube
]
# Sosyal path'lerde tanımlayıcı sanılabilecek gürültü segmentleri.
_WD_SOCIAL_NOISE = {"home", "pages", "page", "profile", "sharer", "watch",
                    "share", "login", "help", "about", "channel", "user", "c"}
# Hedef tipi → P31 (instance of). web/brand → business; person/social → human.
# (Uzman panelde P31 doğrulanır; rapor bunu açıkça not eder.)
_WD_P31 = {"web": "Q4830453", "brand": "Q4830453", "person": "Q5", "social": "Q5"}


def _norm_label(s: str) -> str:
    """Ad eşleştirme normalizasyonu (scoring._norm_label ile aynı davranış —
    o modülü değiştirmemek için yerel kopya). Diakritik + noktalama sıyrılır."""
    s = (s or "").lower().strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s)


async def find_wikidata_entity(name: str) -> str | None:
    """Markanın/kişinin mevcut Wikidata QID'sini bulur (wbsearchentities tr+en).
    scoring._wikidata_presence deseni ama bool yerine QID döner. Ad normalize
    edilerek eşleşir ('GEONI' ~ 'Geoni.ai'). Bulunamaz/ağ düşerse None →
    taslakta CREATE (yeni kayıt); QID dönerse 'mevcut kaydı zenginleştir'."""
    if not name or not name.strip():
        return None
    norm_name = _norm_label(name)
    if len(norm_name) < 3:
        return None
    for lang in ("tr", "en"):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://www.wikidata.org/w/api.php",
                    params={"action": "wbsearchentities", "search": name.strip(),
                            "language": lang, "format": "json", "limit": 5},
                    timeout=8, headers=_UA)
            if resp.status_code != 200:
                continue
            for item in resp.json().get("search", []):
                norm_label = _norm_label(item.get("label", ""))
                if norm_label and (norm_name in norm_label or norm_label in norm_name):
                    qid = (item.get("id") or "").strip()
                    if re.fullmatch(r"Q\d+", qid):
                        return qid
        except Exception as e:
            logger.info(f"find_wikidata_entity failed ({lang}) for '{name}': {e}")
    return None


def _wikidata_name(target: str, audit: dict | None) -> str | None:
    """Deterministik label seçimi: brand_recall.inferred_name (cümle-görünümlü/boş
    değilse), yoksa domain. audit YOKSA None (insana düş — çöp kayıt üretme)."""
    if not audit:
        return None
    result = audit.get("result_json") or {}
    brand = result.get("brand_recall") or {}
    name = _sanitize_text(brand.get("inferred_name") or "", 60)
    if name and 0 < len(name.split()) <= 6:
        return name
    domain = normalize_domain(target)
    if domain:
        return domain
    return None


def _wikidata_p31(atype: str) -> str:
    """Hedef tipi → P31 Q-id (varsayılan business). Uzman panelde doğrulanır."""
    return _WD_P31.get((atype or "web"), "Q4830453")


def _wikidata_social_claims(same_as: list) -> list[tuple[str, str]]:
    """sameAs sosyal URL'lerinden (property, tanımlayıcı) çiftleri — her property
    tek kez, gürültü segmentleri elenir. QuickStatements'te LAST|P...|"id" olur."""
    claims, seen = [], set()
    for u in same_as or []:
        if not isinstance(u, str):
            continue
        for prop, rx in _WD_SOCIAL_PROPS:
            if prop in seen:
                continue
            m = rx.search(u)
            if not m:
                continue
            ident = (m.group(1) or "").strip().strip(".")
            if not ident or ident.lower() in _WD_SOCIAL_NOISE:
                continue
            seen.add(prop)
            claims.append((prop, ident))
            break
    return claims


def _wikidata_notability_sources(audit: dict | None) -> list[str]:
    """Notability kaynak adayları: markayı GERÇEKTEN anan siteler (sov.sources
    own=False + citation_gap). Wikidata kayda-değerlik eşiğini uzman bunlarla
    doğrular; own domain (markanın kendi sitesi) TEK BAŞINA kaynak sayılmaz."""
    result = (audit or {}).get("result_json") or {}
    sov = result.get("sov") or {}
    out, seen = [], set()
    for s in (sov.get("sources") or []):
        if s.get("own"):
            continue
        d = (s.get("domain") or "").strip().lower()
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    for g in (sov.get("citation_gap") or []):
        d = (g.get("domain") if isinstance(g, dict) else g) or ""
        d = d.strip().lower()
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out[:8]


def _qs(s: str) -> str:
    """QuickStatements string değeri güvenliği: çift tırnak/satır sonu kaldır."""
    return re.sub(r"\s{2,}", " ", (s or "").replace('"', "").replace("|", " ")
                  .replace("\n", " ").replace("\t", " ")).strip()


def build_quickstatements(name: str, p31: str, domain: str | None,
                          desc_tr: str | None, desc_en: str | None,
                          social_claims: list, source_url: str, today: str,
                          qid: str | None = None) -> str:
    """Wikidata QuickStatements V1 taslağı (uzman panele yapıştırıp yayınlar).
    qid YOK → CREATE + label(tr/en) + description + P31 + P856(referanslı) + sosyal.
    qid VAR (zenginleştirme) → CREATE/label/description YAZILMAZ (mevcut kaydı
    ezmemek için); yalnız property'ler eklenir. Deterministik — ağ/LLM yok."""
    item = qid if qid else "LAST"
    lines: list[str] = []
    if not qid:
        lines.append("CREATE")
        nm = _qs(name)
        lines.append(f'{item}|Ltr|"{nm}"')
        lines.append(f'{item}|Len|"{nm}"')
        if desc_tr:
            lines.append(f'{item}|Dtr|"{_qs(desc_tr)}"')
        if desc_en:
            lines.append(f'{item}|Den|"{_qs(desc_en)}"')
    if p31:
        lines.append(f"{item}|P31|{p31}")
    if domain:
        ref = ""
        if source_url:
            ref = f'|S854|"{source_url}"|S813|+{today}T00:00:00Z/11'
        lines.append(f'{item}|P856|"https://{domain}"{ref}')
    for prop, ident in social_claims:
        lines.append(f'{item}|{prop}|"{_qs(ident)}"')
    return "\n".join(lines) + "\n"


async def _wikidata_descriptions(name: str, topic: str, atype: str) -> tuple[str | None, str | None]:
    """2-5 kelimelik nötr, reklamsız Wikidata description (tr+en) — TEK LLM çağrısı.
    Düşerse (None, None): QuickStatements'te description satırı yazılmaz (graceful)."""
    type_hint = {"person": "bir kişi", "social": "bir kişi/hesap"}.get(atype, "bir kuruluş/marka")
    prompt = (
        "Wikidata için bir varlık açıklaması (description) yazacaksın. Kurallar: "
        "2-5 KELİME, tekil-belirsiz, NÖTR; reklam/övgü dili YASAK ('lider', 'en iyi', "
        "'önde gelen' KULLANMA). Ör: 'Türk yazılım şirketi' / 'Turkish software company'.\n\n"
        f"Varlık: {name} ({type_hint})\nAlan/konu (hammadde): {topic or '(bilinmiyor)'}\n\n"
        "Yalnız şu iki satırı ver, başka hiçbir şey ekleme:\nTR: <türkçe açıklama>\nEN: <english description>"
    )
    raw = await _llm_text(prompt, max_tokens=120)
    if not raw:
        return None, None
    tr = en = ""
    for line in raw.splitlines():
        low = line.strip().lower()
        if low.startswith("tr:"):
            tr = _sanitize_text(line.split(":", 1)[1], 60)
        elif low.startswith("en:"):
            en = _sanitize_text(line.split(":", 1)[1], 60)
    return (tr or None), (en or None)


def _build_wikidata_report(name: str, qid: str | None, p31: str, domain: str | None,
                           desc_tr: str | None, desc_en: str | None,
                           social_claims: list, sources: list, atype: str,
                           today: str) -> str:
    """Uzmana + müşteriye giden markdown rapor. DÜRÜST 'taslak' etiketi (C-1):
    yayınlamayı İNSAN yapar; notability muhakemesi uzmanda. Sahte 'yapıldı' yok."""
    p31_label = {"Q5": "human (kişi)", "Q4830453": "business (kuruluş)"}.get(p31, p31)
    if qid:
        head = (f"**Mevcut kayıt bulundu:** https://www.wikidata.org/wiki/{qid} — "
                f"iş 'yeni kayıt' değil **zenginleştirme**. Aşağıdaki QuickStatements "
                f"eksik property'leri ekler (label/description'ı EZMEZ).")
    else:
        head = ("**Wikidata kaydı bulunamadı** — taslak yeni kayıt (CREATE) olarak "
                "hazırlandı. Yayından önce notability (kayda-değerlik) doğrulanmalı.")

    rows = [f"| instance of (P31) | {p31_label} | hedef tipi (doğrula) |"]
    if not qid:
        rows.insert(0, f"| label (tr/en) | {name} | tarama |")
        if desc_tr or desc_en:
            rows.append(f"| description | tr: {desc_tr or '—'} · en: {desc_en or '—'} | (nötr, 2-5 kelime) |")
    if domain:
        rows.append(f"| official website (P856) | https://{domain} | site" +
                    (f" + kaynak: {sources[0]}" if sources else "") + " |")
    _prop_name = {"P2002": "X (P2002)", "P2003": "Instagram (P2003)",
                  "P2013": "Facebook (P2013)", "P2397": "YouTube (P2397)",
                  "P4264": "LinkedIn şirket (P4264)", "P6634": "LinkedIn kişi (P6634)",
                  "P7085": "TikTok (P7085)"}
    for prop, ident in social_claims:
        rows.append(f"| {_prop_name.get(prop, prop)} | {ident} | site_assets.sameAs |")

    out = [
        f"# Wikidata Varlık Taslağı — {name}",
        "",
        head,
        "",
        "> ⚠️ Bu bir **taslaktır**. Wikidata'ya yazma bot politikası / çıkar-çatışması "
        "(COI) beyanı gerektirir ve hatalı/erken kayıt SİLİNİR (silme geçmişi müşteriye "
        "zarar verir). Bu yüzden yayınlamayı **uzmanımız** yapar: önce notability'yi "
        "(en az 2 bağımsız kaynak) doğrular, sonra ekteki QuickStatements'ı panele "
        "yapıştırıp yayınlar.",
        "",
        "## Önerilen property'ler",
        "",
        "| Property | Değer | Kaynak |",
        "|----------|-------|--------|",
        *rows,
        "",
    ]
    if sources:
        out += ["## Notability kaynak adayları",
                "AI motorlarının bu markayı/ kişiyi **gerçekten andığı** (kendi siteniz "
                "DIŞI) kaynaklar — uzman bunları kayda-değerlik kanıtı olarak değerlendirir "
                "(her iddiaya S854 referans + S813 tarih):", ""]
        out += [f"- {d}" for d in sources]
        out.append("")
    else:
        out += ["## Notability kaynak adayları",
                "> Taramanızda markayı **bağımsız** anan kaynak bulunamadı. Wikidata eşiği "
                "için pratikte 2+ ciddi bağımsız kaynak gerekir; uzmanımız bunları araştıracak. "
                "Kaynak yetersizse önce **Güvenilir Kaynaklarda Görünürlük** (citation) "
                "hizmetiyle atıf zemini kurulur — doğal zincir: citation → wikidata.", ""]
    out += [
        "## sameAs köprüsü (çift-yönlü doğrulama)",
        "Ekteki **schema-v2.html**, kayıt yayınlanınca Organization `sameAs`'ine Wikidata "
        "bağını ekleyen güncellenmiş şema bloğudur — sitenizdeki mevcut şema bloğuyla "
        "değiştirin. Böylece site ↔ Wikidata ↔ sosyal profiller üçgeni tutarlı olur "
        "(AI entity çözümünün aradığı @id/sameAs zinciri)." if domain else
        "## sameAs köprüsü",
        "> Web siteniz olmadığından şema (schema.org) köprüsü üretilmedi; kişi/marka "
        "hedefinde sosyal profillerin tutarlılığı (aynı isim + aynı açıklama) köprüyü kurar.",
        "",
        "## Nasıl yayınlanır (uzman adımı)",
        "1. QuickStatements aracını aç (quickstatements.toolforge.org).",
        "2. Ekteki `wikidata-quickstatements.txt` içeriğini yapıştır → çalıştır.",
        "3. Notability yetersizse yayınlama; müşteriye kaynak ihtiyacını bildir.",
        "",
        f"> Skor etkisi: kayıt yayınlanınca bir sonraki taramanızda otorite skoruna "
        f"Wikidata bonusu (+8) yansır. (Taslak {today} tarihinde üretildi.)",
    ]
    return "\n".join(out)


_QID_UNSET = object()  # "ağdan bul" sentinel'i (None = 'kayıt yok' anlamından ayrı)


async def generate_wikidata_draft(target: str, audit: dict | None,
                                  qid: object = _QID_UNSET) -> tuple[str | None, str | None]:
    """Yarı-otonom Wikidata taslağı → (rapor_md, quickstatements_txt).
    Deterministik girdiler taramadan (label, P31, P856, sameAs sosyal property'ler,
    notability kaynakları); description için 1 LLM çağrısı (nötr, opsiyonel).
    Yeterli veri yoksa (audit/label yok) (None, None) → insana düş.

    qid: _QID_UNSET (varsayılan) ise find_wikidata_entity ile ağdan bulunur. Çağıran
    (prepare_wikidata_ticket) tek ağ çağrısını paylaşmak için önceden geçer — o zaman
    None de geçerli bir değerdir ('kayıt yok' → CREATE), tekrar ağa çıkılmaz."""
    name = _wikidata_name(target, audit)
    if not name:
        return None, None
    result = (audit or {}).get("result_json") or {}
    brand = result.get("brand_recall") or {}
    atype = (audit or {}).get("type") or "web"
    domain = normalize_domain(target)
    topic = _sanitize_text(brand.get("inferred_topic") or "", 200)
    assets = result.get("site_assets") or {}
    social_claims = _wikidata_social_claims(assets.get("sameAs") or [])
    p31 = _wikidata_p31(atype)
    sources = _wikidata_notability_sources(audit)
    source_url = f"https://{sources[0]}" if sources else ""
    today = date.today().isoformat()

    if qid is _QID_UNSET:  # çağıran geçmedi → kendi ağ çağrımı yap
        qid = await find_wikidata_entity(name)
    qid = qid if (isinstance(qid, str) and re.fullmatch(r"Q\d+", qid)) else None

    desc_tr = desc_en = None
    if not qid:  # description yalnız yeni kayıtta gerekli (zenginleştirmede ezmeyiz)
        desc_tr, desc_en = await _wikidata_descriptions(name, topic, atype)

    quickstatements = build_quickstatements(
        name, p31, domain, desc_tr, desc_en, social_claims, source_url, today, qid=qid)
    report = _build_wikidata_report(
        name, qid, p31, domain, desc_tr, desc_en, social_claims, sources, atype, today)
    return report, quickstatements


async def prepare_wikidata_ticket(ticket_id: int, target: str) -> bool:
    """Yarı-otonom hazırlık (citation_placement deseni): rapor + QuickStatements +
    QID'li schema-v2 bilete düşer, müşteri ANINDA taslağı görür. 'submitted'
    YAPILMAZ — yayınlamayı uzman yapar (Wikidata bot politikası/COI/silme riski).
    Yeterli veri yoksa False → insana devir (dispatch notify_experts'i çağırır)."""
    try:
        audit = await get_latest_audit_by_target(target)
        name = _wikidata_name(target, audit)
        qid = await find_wikidata_entity(name) if name else None
        report, quickstatements = await generate_wikidata_draft(target, audit, qid=qid)
        report = _lint_delivery_message(report or "")
        if not report or not quickstatements:
            await add_ticket_message(ticket_id, None, "system", body=(
                "Wikidata varlık hizmetiniz için önce bir GEONI taraması gerekiyor: "
                "kayıt taslağını (marka adı, konu, sosyal profiller, notability "
                "kaynakları) taramanızın **ölçülmüş verisinden** üretiyoruz — tahminle "
                "çöp kayıt açmak müşteriye zarar verir (silinen kayıt geri gelmez).\n\n"
                "Uzmanımız sizinle iletişime geçip kaydı elle hazırlayacak; dilerseniz "
                "bir tarama yaptırıp bu bilete yazın, taslağı hemen çıkaralım."
            ))
            logger.info(f"prepare_wikidata_ticket: yetersiz veri (t={ticket_id}), uzmana devir")
            return False

        await add_ticket_message(ticket_id, None, "system", body=report)

        qs_url = await upload_ticket_file(
            ticket_id, "wikidata-quickstatements.txt", quickstatements,
            content_type="text/plain; charset=utf-8")
        if qs_url:
            await add_ticket_message(ticket_id, None, "system",
                                     attachment_url=qs_url,
                                     attachment_name="wikidata-quickstatements.txt")

        # M2 sameAs köprüsü: QID'li güncellenmiş schema bloğu (yalnız domain hedefinde).
        domain = normalize_domain(target)
        if domain:
            schema_html = generate_schema_html(domain, audit, wikidata_qid=qid)
            schema_url = await upload_ticket_file(
                ticket_id, "schema-v2.html", schema_html,
                content_type="text/html; charset=utf-8")
            if schema_url:
                await add_ticket_message(ticket_id, None, "system",
                                         attachment_url=schema_url,
                                         attachment_name="schema-v2.html")
        # NOT: mark_ticket_submitted YOK — yayınlama insan işi (yarı-otonom).
        return True
    except Exception as e:
        logger.warning(f"prepare_wikidata_ticket error: {e}")
        return False


def build_expert_audit_context(audit: dict | None) -> dict:
    """A-1: uzman bileti KÖR çalışmasın — taramanın bulgularını (otomasyonun da
    kullandığı deterministik malzeme) kompakt bir özet olarak verir. Uzman panelde
    biletin yanında bunu görür: hangi sorgularda anılmıyor, fırsat/güçlü konular,
    rakipler, citation_gap, AI'ın güvendiği kaynaklar."""
    if not audit:
        return {"available": False}
    result = (audit or {}).get("result_json") or {}
    sov = result.get("sov") or {}
    sel = _select_content_topics(audit)
    brand = result.get("brand_recall") or {}
    return {
        "available": True,
        "target": audit.get("domain") or audit.get("name") or "",
        "type": audit.get("type") or "web",
        "lang": (audit.get("lang") or result.get("lang") or "tr"),
        "score": result.get("score"),
        "inferred_name": _sanitize_text(brand.get("inferred_name") or "", 80),
        "inferred_topic": _sanitize_text(brand.get("inferred_topic") or "", 200),
        "sov_score": sov.get("score"),
        "unmentioned_queries": sel["gaps"],      # AI'ın markayı anmadığı sorgular
        "opportunities": sel["opportunities"],
        "strengths": sel["strengths"],
        "competitors": sel["competitors"],
        "citation_gap": [_sanitize_text(g.get("domain") if isinstance(g, dict) else g, 80)
                         for g in (sov.get("citation_gap") or [])][:10],
        "top_sources": [_sanitize_text(s.get("domain") or "", 80)
                        for s in (sov.get("sources") or []) if not s.get("own")][:10],
        "audit_created_at": audit.get("created_at"),
    }


def _lint_delivery_message(text: str) -> str | None:
    """2.1 ortak teslim korkuluğu: otomasyonun ürettiği müşteri mesajı teslim
    edilmeden ÖNCE denetlenir. Doldurulmamış `{...}` placeholder ya da boş/çok kısa
    gövde = bozuk teslim → None döner (çağıran insana düşürür, çöp göndermez).
    Aksi halde mesajı döner."""
    t = (text or "").strip()
    if len(t) < 20:
        return None
    if re.search(r"\{[a-z_]+\}", t):  # {target}/{robots_txt} gibi dolmamış şablon
        return None
    return t


# Satın alınır alınmaz otomatik TESLİM edilen (submitted) hizmetler.
# llms_robots/schema_setup domain gerektirir (DOMAIN_ONLY); content_package her
# hedef tipinde çalışır (hammaddesini get_latest_audit_by_target'tan alır).
AUTO_FULFILL_KEYS = {"llms_robots", "schema_setup", "content_package"}
# Yarı-otonom: otomasyon istihbarat/taslak HAZIRLAR ama teslimi (submitted) İNSAN
# yapar. Satın almada hazırlık koşar + notify_experts ATLANMAZ. wikidata_entity de
# burada: taslak (QuickStatements + rapor + QID'li schema) üretilir, yayınlama İNSAN.
SEMI_AUTO_KEYS: set[str] = {"citation_placement", "wikidata_entity"}


async def fulfill_auto_ticket(key: str, ticket_id: int, target: str) -> bool:
    """Otomatik teslim dispatcher + SAVUNMA HATTI. Domain-yüzeyi hizmetleri
    (llms_robots/schema — DOMAIN_ONLY_SERVICE_KEYS) yalnız GEÇERLİ bir web sitesine
    uygulanır; target domain değilse (kişi/marka/sosyal ismi/@handle) ÇÖP dosya
    üretmek yerine müşteriden web sitesini ister ve bileti 'open' bırakır. Domain
    gerektirmeyen otomatik hizmetler (content_package) bu kapıya TABİ DEĞİLDİR —
    isim/@handle hedefinde de çalışır (hammaddesini get_latest_audit_by_target'tan alır)."""
    if key in DOMAIN_ONLY_SERVICE_KEYS:
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
    # Domain gerektirmeyen otomatik hizmetler (isim/@handle/domain — hepsi geçerli):
    if key == "content_package":
        return await fulfill_content_ticket(ticket_id, target)
    return False


async def prepare_semi_ticket(key: str, ticket_id: int, target: str) -> bool:
    """Yarı-otonom hazırlık dispatcher: istihbarat/taslak üretir, bilete ekler ama
    'submitted' YAPMAZ (teslimi uzman tamamlar). Çağıran (main.py) bundan bağımsız
    olarak notify_experts_new_task'ı HER ZAMAN çağırır."""
    if key == "citation_placement":
        return await prepare_citation_ticket(ticket_id, target)
    if key == "wikidata_entity":
        return await prepare_wikidata_ticket(ticket_id, target)
    return False


async def fulfill_llms_robots_ticket(ticket_id: int, domain: str) -> bool:
    """purchase_ticket() bu bileti olusturduktan hemen sonra cagirir -
    uzman atama adimi tamamen atlanir. Basarisiz olursa bilet 'open'
    durumunda kalir, admin normal akista bir uzmana atayabilir (sessiz
    dusme - musteri parasini kaybetmez, sadece otomasyon devreye girmemis
    olur)."""
    try:
        audit = await get_latest_web_audit_by_domain(domain)
        sitemap_path = await _find_sitemap(domain, audit)
        robots_txt, robots_status, restricted_bots = await generate_robots_txt(domain, sitemap_path=sitemap_path)
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
            # 4.5/M7: llms.txt'e abartılı "görünürlük artırır" iddiası yüklemeyiz —
            # motorlar dosyayı fiilen tüketmiyor. Değeri: erişim hijyeni + gelecek hazırlığı.
            message += ("\n\n> ✅ Robots kontrolü: robots.txt dosyanız zaten AI botlarının "
                        "erişimine açık — değişiklik gerekmedi. llms.txt bir erişim/hijyen "
                        "dosyasıdır (asıl AI görünürlüğünü içerik ve şema belirler); ileride "
                        "llms.txt'i destekleyen araçlar için sitenizi hazır tutar.")
        elif robots_status == "created":
            message += ("\n\n> ⚠️ Canlı robots.txt'inize ulaşılamadı. Sitenizde ZATEN robots.txt "
                        "VARSA yukarıdaki dosyayla DEĞİŞTİRMEYİN; yalnızca `--- GEONI` bölümünü "
                        "mevcut dosyanızın SONUNA ekleyin.")
        elif robots_status == "needs_manual":
            # BUG-1+2: eklenecek genel blok YOK (tüm kısıtlı botların kendi grubu var);
            # aşağıdaki restricted_bots uyarısı tek başına durumu anlatır.
            pass
        else:  # merged
            message += ("\n\n> Robots: mevcut robots.txt kurallarınız korunarak AI botlarına "
                        "erişim bloğu eklendi; dosyayı olduğu gibi kök dizininize koyabilirsiniz.")
        # BUG-1+2: kendi grubunda kısıtlı botlar — sona eklenen blok bunları geçersiz
        # KILAMAZ (robots.txt en-spesifik grup kazanır; prod Python 3.10 ilk-eşleşme).
        # 'merged' + kısıtlı botlar bir arada olabilir; durumdan bağımsız dürüstçe uyar.
        if restricted_bots:
            bots = ", ".join(restricted_bots[:8])
            message += (f"\n\n> ⚠️ Dikkat: robots.txt'inizde şu AI botları için ZATEN ayrı "
                        f"kurallar var ve bunlar sayfalarınızın bir kısmını engelliyor: "
                        f"**{bots}**. robots.txt'te en özel (bota özel) grup kazandığı için "
                        f"aşağıya eklenen genel izin bloğu bunları AÇMAZ. Bu botlara erişim "
                        f"vermek için, mevcut dosyanızdaki ilgili `User-agent:` gruplarındaki "
                        f"`Disallow:` satırlarını elle gözden geçirmeniz gerekir. İsterseniz bu "
                        f"bilete yazın, hangi satırların kalıp hangilerinin açılması gerektiğini "
                        f"birlikte belirleyelim.")
        # B-4: sitemap kanıtı yoksa dürüst not (satır dosyaya yazılmadı).
        if not sitemap_path:
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
