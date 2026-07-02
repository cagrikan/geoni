"""
GEONI Scanner - Brand Recall Check (v2)

Full pipeline:
1. Playwright → Google'da "[name] [topic]" ara, ilk 8 sonucun başlık+snippet'ini topla
2. Claude (Haiku), ChatGPT (gpt-4o-mini), Gemini (2.5-flash) paralel sorgu
   - Her model: Google sonuçları + kendi bilgisiyle değerlendirme yapar
3. 5 boyutlu skorlama:
   - Claude Tanıma     %20
   - ChatGPT Tanıma    %30
   - Gemini Tanıma     %30
   - Yanıt Kalitesi    %10  (ortalama yanıt uzunluğu/özgüllüğü)
   - Konu Uyumu        %10  (Google sonuç sayısı sinyali)
4. Topic üretimi: güçlü konular + kaçan fırsatlar (ResultsPage formatında)
"""

import asyncio
import os
import re
import logging
import unicodedata

import httpx

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY    = os.environ.get("OPENAI_API_KEY", "")
GOOGLE_API_KEY    = os.environ.get("GOOGLE_API_KEY", "")

WEIGHTS = {
    "claude":          0.20,
    "openai":          0.30,
    "gemini":          0.30,
    "response_quality": 0.10,
    "topic_relevance": 0.10,
}

NOT_RECOGNIZED_PHRASES = [
    "bilmiyorum", "bilgi sahibi değilim", "hakkında bilgim yok",
    "bulamıyorum", "tanımıyorum", "emin değilim", "bilgiye sahip değilim",
    "i don't know", "i'm not sure", "no information", "cannot find",
    "not familiar", "no knowledge", "üzgünüm", "maalesef",
    "yeterli bilgim yok", "elimde bilgi yok",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def _normalize(text: str) -> str:
    text = text.strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text)


def _is_recognized(response: str, name: str) -> bool:
    if not response or len(response.strip()) < 60:
        return False
    norm_resp = _normalize(response)
    for phrase in NOT_RECOGNIZED_PHRASES:
        if phrase in norm_resp:
            return False
    name_tokens = [t for t in _normalize(name).split() if len(t) > 2]
    if name_tokens and not any(t in norm_resp for t in name_tokens):
        return False
    return True


def _response_quality_score(responses: dict) -> float:
    """Score 0-100 based on average response length and specificity."""
    scores = []
    for resp in responses.values():
        if not resp:
            scores.append(0)
            continue
        length = len(resp.strip())
        # 0-100 chars → 0-20, 100-300 → 20-60, 300-600 → 60-90, 600+ → 90-100
        if length < 100:
            scores.append(min(20, length / 5))
        elif length < 300:
            scores.append(20 + (length - 100) / 200 * 40)
        elif length < 600:
            scores.append(60 + (length - 300) / 300 * 30)
        else:
            scores.append(min(100, 90 + (length - 600) / 400 * 10))
    return sum(scores) / max(len(scores), 1)


def _topic_relevance_score(google_results: list, name: str, topic: str) -> float:
    """Score 0-100 based on Google result count and name presence in snippets."""
    if not google_results:
        return 0.0
    # More results = higher baseline
    count_score = min(100, len(google_results) * 12.5)  # 8 results = 100
    # Name appearing in snippets
    name_tokens = [t for t in _normalize(name).split() if len(t) > 2]
    snippet_hits = 0
    for r in google_results:
        snippet_norm = _normalize(r.get("snippet", "") + r.get("title", ""))
        if any(t in snippet_norm for t in name_tokens):
            snippet_hits += 1
    snippet_score = (snippet_hits / max(len(google_results), 1)) * 100
    return (count_score + snippet_score) / 2


# ── Google search via Playwright ────────────────────────────────────────────

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")

async def _google_search(name: str, topic: str, max_results: int = 8, tavily_query: str = "") -> list:
    """
    Search via Tavily API — optimized for AI/RAG workflows.
    Uses enriched query if provided, otherwise builds simple name+topic query.
    Returns list of {title, snippet, url} dicts.
    """
    if not TAVILY_API_KEY:
        logger.warning("TAVILY_API_KEY not configured, skipping search")
        return []

    query = tavily_query or (f"{name} {topic}".strip() if topic and topic != name else name)

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",
                    "include_answer": False,
                    "include_raw_content": False,
                },
                headers={
                    "Authorization": f"Bearer {TAVILY_API_KEY}",
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            if resp.status_code != 200:
                logger.warning(f"Tavily {resp.status_code}: {resp.text[:200]}")
                return []

            items = resp.json().get("results", [])
            results = []
            for item in items:
                results.append({
                    "title": item.get("title", "").strip(),
                    "snippet": item.get("content", "").strip()[:300],
                    "url": item.get("url", ""),
                })
            logger.info(f"Tavily search for '{query}' returned {len(results)} results")
            return results

    except Exception as e:
        logger.warning(f"Tavily search failed: {e}")
        return []


# ── Model query functions ────────────────────────────────────────────────────

def _build_prompt(name: str, topic: str, google_results: list) -> str:
    """Build enriched prompt with Google context."""
    topic_part = f" ({topic} alanında)" if topic and topic.strip().lower() != name.strip().lower() else ""

    if google_results:
        snippets = "\n".join(
            f"- {r['title']}: {r['snippet']}" for r in google_results[:6] if r.get("title")
        )
        context = f"\n\nGoogle'dan bu kişi hakkında bulunan bilgiler:\n{snippets}\n\n"
    else:
        context = "\n\n(Google'da bu kişi hakkında sonuç bulunamadı.)\n\n"

    return (
        f"{name}{topic_part} kimdir?{context}"
        f"Bu bilgilere ve kendi bilgine dayanarak {name} hakkında Türkçe olarak değerlendir. "
        f"Eğer hakkında hiçbir bilgi yoksa bunu açıkça belirt."
    )


async def _ask_claude(prompt: str) -> str | None:
    if not ANTHROPIC_API_KEY:
        return None
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": "claude-haiku-4-5", "max_tokens": 400, "messages": [{"role": "user", "content": prompt}]},
                timeout=30,
            )
            if r.status_code == 200:
                blocks = r.json().get("content", [])
                return "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
            logger.warning(f"Claude {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.warning(f"Claude query failed: {e}")
    return None


async def _ask_openai(prompt: str) -> str | None:
    if not OPENAI_API_KEY:
        return None
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}], "max_tokens": 400, "temperature": 0.3},
                timeout=30,
            )
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
            logger.warning(f"OpenAI {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.warning(f"OpenAI query failed: {e}")
    return None


async def _ask_gemini(prompt: str) -> str | None:
    if not GOOGLE_API_KEY:
        return None
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GOOGLE_API_KEY}",
                json={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                      "generationConfig": {"maxOutputTokens": 400, "temperature": 0.3}},
                timeout=30,
            )
            if r.status_code == 200:
                parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                return " ".join(p.get("text", "") for p in parts).strip()
            logger.warning(f"Gemini {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.warning(f"Gemini query failed: {e}")
    return None


# ── Topic generation ─────────────────────────────────────────────────────────

async def _generate_brand_topics(name: str, topic: str, google_results: list, responses: dict) -> dict:
    """
    Generate performing topics and opportunities in the same format as
    domain audit topics, using the Google results and model responses as context.
    """
    if not ANTHROPIC_API_KEY:
        return {"performing_topics": [], "opportunity_topics": []}

    context_parts = []
    if google_results:
        context_parts.append("Arama sonuçları:\n" + "\n".join(
            f"- {r['title']}: {r['snippet']} ({r['url']})" for r in google_results[:5] if r.get("title")
        ))
    for model, resp in responses.items():
        if resp:
            context_parts.append(f"{model} değerlendirmesi:\n{resp[:300]}")

    context = "\n\n".join(context_parts)
    topic_context = f" ({topic} alanında)" if topic and topic != name else ""

    prompt = (
        f"IMPORTANT: Respond entirely in Turkish.\n\n"
        f"{name}{topic_context} için AI görünürlük analizi yapıyoruz.\n\n"
        f"Mevcut bilgiler:\n{context}\n\n"
        f"Lütfen şu formatta JSON döndür (başka hiçbir şey yazma):\n"
        f'{{"performing_topics": [{{"topic": "...", "mentions": 0, "platforms": ["chatgpt", "claude"], "source_url": "https://..."}}], '
        f'"opportunity_topics": [{{"topic": "...", "mentions": 0, "platforms": [], "competitors": ["rakip1.com", "rakip2.com"]}}]}}\n\n'
        f"performing_topics: Bu kişinin güçlü olduğu, AI motorlarında görünür olduğu 3-4 konu. "
        f"source_url alanına arama sonuçlarından en alakalı URL'yi koy (varsa). Yoksa boş string koy.\n"
        f"opportunity_topics: Bu kişinin eksik olduğu, rakiplerin görünür olduğu 4-5 fırsat konusu. "
        f"Konular {topic if topic else 'genel'} alanıyla ilgili olsun. competitors alanına gerçek Türkiye'deki rakip site/kurum domainleri yaz."
    )

    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": "claude-haiku-4-5", "max_tokens": 800, "messages": [{"role": "user", "content": prompt}]},
                timeout=30,
            )
            if r.status_code == 200:
                blocks = r.json().get("content", [])
                raw = "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
                raw = re.sub(r"```json|```", "", raw).strip()
                data = __import__("json").loads(raw)
                return {
                    "performing_topics": data.get("performing_topics", []),
                    "opportunity_topics": data.get("opportunity_topics", []),
                }
    except Exception as e:
        logger.warning(f"Brand topic generation failed: {e}")

    return {"performing_topics": [], "opportunity_topics": []}


def _granular_model_score(response: str, name: str, via_web: bool = False) -> float:
    """
    Returns a granular score for a single model's response:
    - Not recognized: 0
    - Recognized via web data only (failover): 10-20
    - Recognized parametrically: 30-90 based on response quality
    """
    if not _is_recognized(response, name):
        return 0.0

    if via_web:
        # Recognized only after web data injection → low score
        length = len(response.strip())
        return min(20, 10 + (length / 500) * 10)

    # Parametric recognition → granular 30-90 based on response quality
    length = len(response.strip())
    if length < 150:
        return 30.0
    elif length < 300:
        return 30 + (length - 150) / 150 * 20  # 30-50
    elif length < 500:
        return 50 + (length - 300) / 200 * 20  # 50-70
    else:
        return min(90, 70 + (length - 500) / 500 * 20)  # 70-90


async def _check_model_two_phase(name: str, topic: str, web_results: list, ask_fn) -> dict:
    """
    Two-phase recognition for a single model:
    Phase 1: Ask without web data (parametric test)
    Phase 2: If not recognized, ask with web data (failover)
    Returns: {recognized, score, response, via_web}
    """
    topic_part = f" ({topic} alanında)" if topic and topic.strip().lower() != name.strip().lower() else ""

    # Phase 1: Parametric query (no web data)
    prompt_p1 = (
        f"{name}{topic_part} kimdir? "
        f"Kendi bilgine dayanarak Türkçe olarak anlat. "
        f"Eğer hakkında hiçbir bilgin yoksa bunu açıkça belirt."
    )
    resp_p1 = await ask_fn(prompt_p1)

    if resp_p1 and _is_recognized(resp_p1, name):
        score = _granular_model_score(resp_p1, name, via_web=False)
        return {"recognized": True, "score": score, "response": resp_p1, "via_web": False}

    # Phase 2: Failover with web data (only if Tavily found results)
    if web_results:
        snippets = "\n".join(
            f"- {r['title']}: {r['snippet']}" for r in web_results[:5] if r.get("title")
        )
        prompt_p2 = (
            f"{name}{topic_part} kimdir?\n\n"
            f"Web'den bulunan bilgiler:\n{snippets}\n\n"
            f"Bu bilgilere dayanarak {name} hakkında Türkçe olarak değerlendir. "
            f"Eğer hakkında hâlâ bilgi yoksa bunu açıkça belirt."
        )
        resp_p2 = await ask_fn(prompt_p2)

        if resp_p2 and _is_recognized(resp_p2, name):
            score = _granular_model_score(resp_p2, name, via_web=True)
            return {"recognized": True, "score": score, "response": resp_p2, "via_web": True}

    return {"recognized": False, "score": 0.0, "response": resp_p1, "via_web": False}


# ── Public API ───────────────────────────────────────────────────────────────

def _build_tavily_query(name: str, topic: str = "", role: str = "", company: str = "",
                         sector: str = "", location: str = "", linkedin_url: str = "",
                         website: str = "", entity_type: str = "person") -> str:
    """
    Build an enriched Tavily search query to minimize adaş (namesake) confusion.
    Format: "Name" AND ("Role" OR "Company" OR "Location")
    If LinkedIn URL provided, use it directly as the query.
    """
    # LinkedIn is only used if publicly accessible (checked separately)
    # Base: exact name in quotes
    base = f'"{name}"'

    # Collect context signals
    signals = []
    if role:     signals.append(role)
    if company:  signals.append(company)
    if sector:   signals.append(sector)
    if location: signals.append(location)
    if topic:    signals.append(topic)
    if website:  signals.append(website)

    if signals:
        or_part = " OR ".join(f'"{s}"' if " " in s else s for s in signals[:4])
        return f'{base} AND ({or_part})'
    return base


async def check_brand_recall(
    name: str,
    topic: str = "",
    email: str = "",
    role: str = "",
    company: str = "",
    sector: str = "",
    location: str = "",
    linkedin_url: str = "",
    website: str = "",
    entity_type: str = "person",
) -> dict:
    """
    Full brand recall pipeline:
    1. Tavily web search
    2. Two-phase parallel model queries (parametric → web failover)
    3. Granular scoring (30-90 parametric, 10-20 via web, 0 not recognized)
    4. Topic generation
    """
    if not any([ANTHROPIC_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY]):
        return {"recognized": False, "score": None, "topic": topic, "raw_list": None,
                "checked": False, "model_results": {}, "performing_topics": [], "opportunity_topics": []}

    # Step 1: Tavily web search with enriched query
    tavily_query = _build_tavily_query(name, topic, role, company, sector, location, "", website, entity_type)
    web_results = await _google_search(name, topic, tavily_query=tavily_query)

    # Step 1b: If LinkedIn URL provided, check if it's publicly accessible
    if linkedin_url:
        try:
            async with httpx.AsyncClient() as c:
                r = await c.get(linkedin_url, timeout=8, follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code == 200 and "linkedin.com" in r.url.host:
                    # Public profile — add as extra Tavily search
                    linkedin_results = await _google_search(name, topic, tavily_query=linkedin_url)
                    # Merge, avoid duplicates
                    existing_urls = {r['url'] for r in web_results}
                    for lr in linkedin_results:
                        if lr['url'] not in existing_urls:
                            web_results.append(lr)
                    logger.info(f"LinkedIn public, added {len(linkedin_results)} results")
                else:
                    logger.info(f"LinkedIn profile not public ({r.status_code}), skipping")
        except Exception as e:
            logger.info(f"LinkedIn check failed: {e}, skipping")

    # Step 1b: Identity verification (only if web results found and context given)
    has_context = any([role, company, location, sector])
    if web_results and has_context and OPENAI_API_KEY:
        context_parts = []
        if role:     context_parts.append(f"Unvan: {role}")
        if company:  context_parts.append(f"Şirket: {company}")
        if location: context_parts.append(f"Şehir: {location}")
        if sector:   context_parts.append(f"Sektör: {sector}")
        if topic:    context_parts.append(f"Alan: {topic}")
        user_context = ", ".join(context_parts)
        snippets = "\n".join(f"- {r['title']}: {r['snippet']}" for r in web_results[:5] if r.get("title"))
        verify_prompt = (
            f"Kullanıcı şu kişiyi arıyor: {name} ({user_context}).\n\n"
            f"İnternetten toplanan sonuçlar:\n{snippets}\n\n"
            f"Bu sonuçların aradığımız kişiyle eşleşme olasılığı 0-100 arasında kaç?\n"
            f"Sadece JSON döndür: {{\"match\": <0-100>}}"
        )
        try:
            import json as _json
            async with httpx.AsyncClient() as c:
                vr = await c.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                    json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": verify_prompt}],
                          "max_tokens": 50, "temperature": 0, "response_format": {"type": "json_object"}},
                    timeout=15,
                )
                if vr.status_code == 200:
                    vdata = _json.loads(vr.json()["choices"][0]["message"]["content"])
                    match_score = int(vdata.get("match", 100))
                    if match_score < 70:
                        logger.info(f"Identity mismatch for '{name}': match_score={match_score}")
                        return {
                            "identity_mismatch": True,
                            "match_score": match_score,
                            "recognized": False,
                            "score": 0,
                            "checked": True,
                            "model_results": {},
                            "performing_topics": [],
                            "opportunity_topics": [],
                        }
        except Exception as e:
            logger.warning(f"Identity verification failed: {e}")

    # Step 2: Two-phase parallel queries
    claude_res, openai_res, gemini_res = await asyncio.gather(
        _check_model_two_phase(name, topic, web_results, _ask_claude),
        _check_model_two_phase(name, topic, web_results, _ask_openai),
        _check_model_two_phase(name, topic, web_results, _ask_gemini),
        return_exceptions=True,
    )

    def safe(r):
        if isinstance(r, Exception):
            return {"recognized": False, "score": 0.0, "response": None, "via_web": False}
        return r

    results = {
        "claude": safe(claude_res),
        "openai": safe(openai_res),
        "gemini": safe(gemini_res),
    }

    model_results = {
        "claude": {
            "recognized": results["claude"]["recognized"],
            "score": results["claude"]["score"],
            "via_web": results["claude"]["via_web"],
            "model": "Claude",
        },
        "openai": {
            "recognized": results["openai"]["recognized"],
            "score": results["openai"]["score"],
            "via_web": results["openai"]["via_web"],
            "model": "ChatGPT",
        },
        "gemini": {
            "recognized": results["gemini"]["recognized"],
            "score": results["gemini"]["score"],
            "via_web": results["gemini"]["via_web"],
            "model": "Gemini",
        },
    }

    # Step 3: Scoring
    claude_score  = results["claude"]["score"]
    openai_score  = results["openai"]["score"]
    gemini_score  = results["gemini"]["score"]

    responses = {
        "claude": results["claude"]["response"],
        "openai": results["openai"]["response"],
        "gemini": results["gemini"]["response"],
    }

    quality_score   = _response_quality_score(responses)
    relevance_score = _topic_relevance_score(web_results, name, topic)

    overall_score = int(round(
        claude_score    * WEIGHTS["claude"] +
        openai_score    * WEIGHTS["openai"] +
        gemini_score    * WEIGHTS["gemini"] +
        quality_score   * WEIGHTS["response_quality"] +
        relevance_score * WEIGHTS["topic_relevance"]
    ))

    score_breakdown = {
        "claude":         round(claude_score, 1),
        "chatgpt":        round(openai_score, 1),
        "gemini":         round(gemini_score, 1),
        "yanit_kalitesi": round(quality_score, 1),
        "konu_uyumu":     round(relevance_score, 1),
    }

    recognition_count = sum(1 for v in model_results.values() if v["recognized"])

    # Step 4: Topic generation
    topics = await _generate_brand_topics(name, topic, web_results, responses)

    # Build raw_list for display
    raw_parts = []
    for key in ["claude", "openai", "gemini"]:
        resp = results[key]["response"]
        if resp:
            via = " (web verisiyle)" if results[key]["via_web"] else ""
            raw_parts.append(f"[{model_results[key]['model']}{via}]\n{resp}")
    raw_list = "\n\n".join(raw_parts) if raw_parts else None

    logger.info(f"Brand recall for '{name}': score={overall_score}, {recognition_count}/3 models, {len(web_results)} web results")

    return {
        "recognized": recognition_count > 0,
        "recognition_count": recognition_count,
        "score": overall_score,
        "score_breakdown": score_breakdown,
        "topic": topic,
        "raw_list": raw_list,
        "model_results": model_results,
        "google_result_count": len(web_results),
        "performing_topics": topics["performing_topics"],
        "opportunity_topics": topics["opportunity_topics"],
        "checked": True,
    }


async def infer_brand_identity(domain: str, page_titles: list[str]) -> dict:
    """Infer brand name + topic from crawled page titles."""
    fallback_name = domain.split(".")[0].replace("-", " ").title()

    if not page_titles or not ANTHROPIC_API_KEY:
        return {"name": fallback_name, "topic": fallback_name}

    titles_text = "\n".join(page_titles[:10])
    prompt = (
        f"Aşağıda bir web sitesinden alınan sayfa başlıkları var. "
        f"Şirketin/markanın adını ve faaliyet alanını tahmin et.\n\n"
        f"Sayfa başlıkları:\n{titles_text}\n\n"
        f"Sadece şu formatta yanıt ver:\n"
        f"MARKA: [marka/şirket adı]\n"
        f"ALAN: [faaliyet alanı, 2-4 kelime]"
    )

    try:
        raw = await _ask_claude(prompt)
        if not raw:
            return {"name": fallback_name, "topic": fallback_name}
        name_m  = re.search(r"MARKA:\s*(.+)", raw)
        topic_m = re.search(r"ALAN:\s*(.+)", raw)
        return {
            "name":  name_m.group(1).strip()  if name_m  else fallback_name,
            "topic": topic_m.group(1).strip() if topic_m else fallback_name,
        }
    except Exception as e:
        logger.warning(f"Brand identity inference failed: {e}")
        return {"name": fallback_name, "topic": fallback_name}
