"""
GEONI Scanner - Brand Recall Check
Queries Claude, ChatGPT, and Gemini in parallel with a direct
"who is X?" question. If the model returns a meaningful, specific
answer about the person/brand, they are recognized. Vague or
"I don't know" responses count as not recognized.

Score mapping:
  3/3 models recognize → 100
  2/3 models recognize →  65
  1/3 models recognize →  33
  0/3 models recognize →   0
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

SCORE_MAP = {3: 100, 2: 65, 1: 33, 0: 0}

# Phrases that indicate the model does NOT recognize the person
NOT_RECOGNIZED_PHRASES = [
    "bilmiyorum", "bilgi sahibi değilim", "hakkında bilgim yok",
    "bulamıyorum", "tanımıyorum", "emin değilim",
    "i don't know", "i'm not sure", "no information",
    "cannot find", "not familiar", "no knowledge",
    "üzgünüm", "maalesef", "yeterli bilgim",
]


def _normalize(text: str) -> str:
    text = text.strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text)


def _is_recognized(response: str, name: str) -> bool:
    """
    A response counts as 'recognized' if:
    1. It's long enough to be substantive (>80 chars)
    2. It doesn't contain "I don't know" type phrases
    3. Part of the name appears in the response (confirms it's about the right person)
    """
    if not response or len(response.strip()) < 80:
        return False

    norm_resp = _normalize(response)

    # Check for not-recognized phrases
    for phrase in NOT_RECOGNIZED_PHRASES:
        if phrase in norm_resp:
            return False

    # Check that at least one name token appears in the response
    name_tokens = [t for t in _normalize(name).split() if len(t) > 2]
    if name_tokens and not any(t in norm_resp for t in name_tokens):
        return False

    return True


GENERIC_EMAIL_PROVIDERS = {
    "gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "yandex.com",
    "yandex.ru", "icloud.com", "me.com", "live.com", "msn.com",
    "hotmail.com.tr", "yahoo.com.tr"
}


def _extract_corporate_domain(email: str) -> str | None:
    """Return domain from email if it's a corporate (non-generic) address."""
    if not email or "@" not in email:
        return None
    domain = email.split("@")[-1].strip().lower()
    if domain in GENERIC_EMAIL_PROVIDERS:
        return None
    return domain


def _build_query(name: str, topic: str, email: str = "") -> str:
    parts = [f"{name} kimdir?"]
    if topic and topic.strip().lower() != name.strip().lower():
        parts.append(f"{topic} alanında çalışmaktadır.")
    corporate_domain = _extract_corporate_domain(email)
    if corporate_domain:
        parts.append(f"Web sitesi: {corporate_domain}.")
    parts.append("Bu kişi veya kurum hakkında bildiklerini Türkçe olarak anlat.")
    parts.append("Eğer hakkında hiçbir bilgin yoksa bunu açıkça belirt.")
    return " ".join(parts)


async def _ask_claude(prompt: str) -> str | None:
    if not ANTHROPIC_API_KEY:
        return None
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": "claude-sonnet-4-6", "max_tokens": 300, "messages": [{"role": "user", "content": prompt}]},
                timeout=25,
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
                json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}], "max_tokens": 300, "temperature": 0.3},
                timeout=25,
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
                      "generationConfig": {"maxOutputTokens": 300, "temperature": 0.3}},
                timeout=25,
            )
            if r.status_code == 200:
                parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                return " ".join(p.get("text", "") for p in parts).strip()
            logger.warning(f"Gemini {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.warning(f"Gemini query failed: {e}")
    return None


async def check_brand_recall(name: str, topic: str, email: str = "") -> dict:
    """Query all three models in parallel with a direct 'who is X?' question."""
    if not any([ANTHROPIC_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY]):
        return {"recognized": False, "score": None, "topic": topic, "raw_list": None,
                "checked": False, "model_results": {}}

    query = _build_query(name, topic, email)

    claude_resp, openai_resp, gemini_resp = await asyncio.gather(
        _ask_claude(query),
        _ask_openai(query),
        _ask_gemini(query),
        return_exceptions=True,
    )

    def safe(r):
        return r if isinstance(r, str) else None

    claude_resp  = safe(claude_resp)
    openai_resp  = safe(openai_resp)
    gemini_resp  = safe(gemini_resp)

    model_results = {
        "claude": {"recognized": _is_recognized(claude_resp, name),  "model": "Claude"},
        "openai": {"recognized": _is_recognized(openai_resp, name),  "model": "ChatGPT"},
        "gemini": {"recognized": _is_recognized(gemini_resp, name),  "model": "Gemini"},
    }

    recognition_count = sum(1 for v in model_results.values() if v["recognized"])
    score = SCORE_MAP.get(recognition_count, 0)

    raw_parts = []
    for key, resp in [("claude", claude_resp), ("openai", openai_resp), ("gemini", gemini_resp)]:
        if resp:
            raw_parts.append(f"[{model_results[key]['model']}]\n{resp}")
    raw_list = "\n\n".join(raw_parts) if raw_parts else None

    logger.info(f"Brand recall for '{name}': {recognition_count}/3 models recognized ({score} pts)")

    return {
        "recognized": recognition_count > 0,
        "recognition_count": recognition_count,
        "score": score,
        "topic": topic,
        "raw_list": raw_list,
        "model_results": model_results,
        "checked": True,
    }


async def infer_brand_identity(domain: str, page_titles: list[str]) -> dict:
    """Infer brand name + topic from crawled page titles."""
    fallback_name = domain.split(".")[0].replace("-", " ").title()

    if not page_titles or not any([ANTHROPIC_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY]):
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
        raw = (await _ask_claude(prompt)) or (await _ask_openai(prompt)) or (await _ask_gemini(prompt))
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
