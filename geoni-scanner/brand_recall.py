"""
GEONI Scanner - Brand Recall Check
Tests whether the LLM already recognizes a person/company by name within a
given topic, based on its trained (parametric) knowledge — independent of
whether it can crawl/retrieve their website. This is a different signal from
the site-crawl-based score: it measures "does AI know who you are" rather
than "can AI find fresh info about you."

Uses the same prompt pattern as the geoni.ai landing page widget, so results
are consistent whether a person queries themselves directly (no website) or
this runs automatically as part of a full domain audit.
"""

import os
import re
import logging
import unicodedata

import httpx

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

SYSTEM_PROMPT = (
    "Sen yardımcı bir yapay zeka asistanısın. Kullanıcı sana bir alan veya konu "
    "hakkında sorduğunda, o alanda Türkiye'de bilinen veya öne çıkan gerçek "
    "kişileri, şirketleri ya da kurumları listele. Mutlaka somut isimler ver — "
    "\"bilmiyorum\" veya \"bu alanda kimse yok\" deme, o alanda kim varsa onu yaz. "
    "Her satırda \"• [İsim/Kurum] — [ne yaptığı veya bu alandaki rolü]\" formatında "
    "yaz. 4-6 isim sun. Markdown kullanma. Türkçe yanıtla."
)


def _normalize(text: str) -> str:
    """Lowercase + strip accents/diacritics for forgiving name matching."""
    text = text.strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text)


def _name_appears_in_list(name: str, list_text: str) -> bool:
    """
    Fuzzy check: does the target name appear in the generated list?
    Matches on normalized substring, and also checks individual name tokens
    (e.g. "Sabri Çağrı Çakır" matches if "Çağrı Çakır" or "Sabri Çakır" appears)
    so minor formatting differences don't cause false negatives.
    """
    normalized_name = _normalize(name)
    normalized_list = _normalize(list_text)

    if normalized_name in normalized_list:
        return True

    tokens = [t for t in normalized_name.split(" ") if len(t) > 2]
    if len(tokens) >= 2:
        # Require at least 2 distinct name tokens to appear for a match,
        # to avoid false positives on very common single words.
        matches = sum(1 for t in tokens if t in normalized_list)
        if matches >= 2:
            return True

    return False


async def check_brand_recall(name: str, topic: str) -> dict:
    """
    Two-step brand recognition check:
    1. Query a list of known people/companies in the topic, check if name appears
    2. If not found in list, do a direct "do you know this person" query
    Uses Sonnet for better accuracy on person/brand recognition.
    """
    if not ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not configured, skipping brand recall check")
        return {"recognized": False, "score": None, "topic": topic, "raw_list": None, "checked": False}

    use_direct_query = not topic.strip() or topic.strip().lower() == name.strip().lower()

    try:
        async with httpx.AsyncClient() as client:
            # Step 1: List-based query (unless no topic provided)
            raw_list = None
            recognized = False

            if not use_direct_query:
                query = f"{topic} alanında Türkiye'de öne çıkan kişiler ve firmalar kimlerdir? Gerçek isimler ver."
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-sonnet-4-6",
                        "max_tokens": 400,
                        "system": SYSTEM_PROMPT,
                        "messages": [{"role": "user", "content": query}],
                    },
                    timeout=20,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    text_blocks = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
                    raw_list = "\n".join(text_blocks).strip()
                    recognized = _name_appears_in_list(name, raw_list)

            # Step 2: Direct recognition query (always run if not found in list)
            if not recognized:
                topic_context = f" ({topic} alanında)" if topic and not use_direct_query else ""
                direct_query = (
                    f"Türkiye'de '{name}' adlı kişi veya kurum{topic_context} hakkında ne biliyorsun? "
                    f"Bu kişi/kurum Türkiye'de tanınan, bilinen biri mi?\n"
                    f"Cevabını şu formatta ver:\n"
                    f"TANINIYOR: evet veya hayır\n"
                    f"AÇIKLAMA: [kısa açıklama, 1-2 cümle, Türkçe]"
                )
                resp2 = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-sonnet-4-6",
                        "max_tokens": 200,
                        "messages": [{"role": "user", "content": direct_query}],
                    },
                    timeout=20,
                )
                if resp2.status_code == 200:
                    data2 = resp2.json()
                    text_blocks2 = [b.get("text", "") for b in data2.get("content", []) if b.get("type") == "text"]
                    direct_response = "\n".join(text_blocks2).strip()
                    direct_recognized = "taniniyor: evet" in _normalize(direct_response) or "tanınıyor: evet" in _normalize(direct_response)
                    if direct_recognized:
                        recognized = True
                        # Append direct response to raw_list for display
                        raw_list = (raw_list + "\n\n" + direct_response) if raw_list else direct_response

            return {
                "recognized": recognized,
                "score": 100 if recognized else 0,
                "topic": topic,
                "raw_list": raw_list,
                "checked": True,
            }

    except Exception as e:
        logger.warning(f"Brand recall check failed: {e}")
        return {"recognized": False, "score": None, "topic": topic, "raw_list": None, "checked": False}


async def infer_brand_identity(domain: str, page_titles: list[str]) -> dict:
    """
    For a full domain audit (no explicit name/topic given by the user), infer
    a likely company/brand name and industry topic from the crawled page
    titles, so the brand recall check can run automatically.

    Falls back to a simple heuristic (domain name as brand, generic topic)
    if the LLM call fails or no key is configured.
    """
    fallback_name = domain.split(".")[0].replace("-", " ").title()

    if not ANTHROPIC_API_KEY or not page_titles:
        return {"name": fallback_name, "topic": fallback_name}

    titles_text = "\n".join(page_titles[:10])
    prompt = (
        f"Aşağıda bir web sitesinden alınan sayfa başlıkları var. Bu bilgilere dayanarak "
        f"şirketin/markanın adını ve hangi sektörde/alanda faaliyet gösterdiğini tahmin et.\n\n"
        f"Sayfa başlıkları:\n{titles_text}\n\n"
        f"Sadece şu formatta yanıt ver, başka hiçbir şey yazma:\n"
        f"MARKA: [marka/şirket adı]\n"
        f"ALAN: [faaliyet alanı, 2-4 kelime]"
    )

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-6",
                    "max_tokens": 100,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=15,
            )
            if resp.status_code != 200:
                return {"name": fallback_name, "topic": fallback_name}

            data = resp.json()
            text_blocks = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
            raw = "\n".join(text_blocks)

            name_match = re.search(r"MARKA:\s*(.+)", raw)
            topic_match = re.search(r"ALAN:\s*(.+)", raw)

            name = name_match.group(1).strip() if name_match else fallback_name
            topic = topic_match.group(1).strip() if topic_match else fallback_name

            return {"name": name, "topic": topic}

    except Exception as e:
        logger.warning(f"Brand identity inference failed: {e}")
        return {"name": fallback_name, "topic": fallback_name}
