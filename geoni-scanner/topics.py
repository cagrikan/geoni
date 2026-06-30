"""
GEONI Scanner - Topic Generation Service
Uses an LLM to identify performing topics (where the brand likely shows up
in AI answers) and opportunity topics (where competitors show up but the
brand doesn't), based on crawled page content.

Multi-provider with automatic fallback chain:
  1. Anthropic (Claude) - if ANTHROPIC_API_KEY is set
  2. OpenAI (GPT)        - if OPENAI_API_KEY is set
  3. Google (Gemini)     - if GOOGLE_API_KEY is set
  4. Static heuristic fallback - if no provider is configured/working

Add more providers by implementing a `_call_<provider>()` function with the
same signature and registering it in PROVIDER_CHAIN.
"""

import json
import logging
import os
import re

import httpx

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")

PROMPT_TEMPLATE = """IMPORTANT: Respond entirely in Turkish (Türkçe). All topic names, descriptions, and text must be in Turkish.

You are an AI visibility analyst. Given a website's crawled page titles \
and descriptions below, identify:

1. "performing_topics": 3-5 topics/questions this site likely already answers well \
   (i.e. where it would plausibly be cited by ChatGPT, Claude, or Perplexity).
2. "opportunity_topics": 3-5 adjacent topics/questions in the same space that this \
   site does NOT appear to cover, where competitors in this niche likely DO show up.

Domain: {domain}

Crawled pages (title | meta description):
{page_summaries}

Respond ONLY with valid JSON in this exact shape, no preamble, no markdown fences:
{{
  "performing_topics": [{{"topic": "...", "mentions": 0, "platforms": ["chatgpt","perplexity"]}}],
  "opportunity_topics": [{{"topic": "...", "mentions": 0, "platforms": [], "competitors": []}}]
}}
"""


def _build_prompt(domain: str, pages: list[dict]) -> str:
    summaries = []
    for p in pages[:20]:  # cap context size
        title = p.get("title", "").strip()
        desc = p.get("meta_description", "").strip()
        if title or desc:
            summaries.append(f"- {title} | {desc}")
    page_summaries = "\n".join(summaries) if summaries else "(no page metadata available)"
    return PROMPT_TEMPLATE.format(domain=domain, page_summaries=page_summaries)


def _extract_json(text: str) -> dict | None:
    """Extract a JSON object from an LLM response, tolerant of stray text/fences."""
    text = text.strip()
    text = re.sub(r"^```(json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                return None
    return None


async def _call_anthropic(prompt: str) -> dict | None:
    if not ANTHROPIC_API_KEY:
        return None
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
                    "model": "claude-haiku-4-5",
                    "max_tokens": 1000,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                text = "".join(
                    block.get("text", "") for block in data.get("content", [])
                    if block.get("type") == "text"
                )
                return _extract_json(text)
            else:
                logger.warning(f"Anthropic API error {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"Anthropic call failed: {e}")
    return None


async def _call_openai(prompt: str) -> dict | None:
    if not OPENAI_API_KEY:
        return None
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 1000,
                },
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                text = data["choices"][0]["message"]["content"]
                return _extract_json(text)
            else:
                logger.warning(f"OpenAI API error {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"OpenAI call failed: {e}")
    return None


async def _call_gemini(prompt: str) -> dict | None:
    if not GOOGLE_API_KEY:
        return None
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GOOGLE_API_KEY}",
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                return _extract_json(text)
            else:
                logger.warning(f"Gemini API error {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"Gemini call failed: {e}")
    return None


PROVIDER_CHAIN = [
    ("anthropic", _call_anthropic),
    ("openai", _call_openai),
    ("gemini", _call_gemini),
]


def _static_fallback(domain: str, pages: list[dict]) -> dict:
    """Last-resort heuristic when no LLM provider is configured or all fail."""
    titles = [p.get("title", "") for p in pages if p.get("title")]
    performing = [
        {"topic": t, "mentions": 0, "platforms": []}
        for t in titles[:3]
    ] if titles else []
    return {
        "performing_topics": performing,
        "opportunity_topics": [],
    }


async def generate_topics_and_opportunities(domain: str, pages: list[dict]) -> dict:
    """
    Generate performing/opportunity topics using the first available LLM provider.
    Falls back to a static heuristic if none are configured or all calls fail.
    """
    prompt = _build_prompt(domain, pages)

    for name, fn in PROVIDER_CHAIN:
        result = await fn(prompt)
        if result and "performing_topics" in result:
            logger.info(f"Topic generation succeeded via {name}")
            return {
                "performing_topics": result.get("performing_topics", []),
                "opportunity_topics": result.get("opportunity_topics", []),
            }

    logger.warning("No LLM provider available/succeeded — using static fallback")
    return _static_fallback(domain, pages)
