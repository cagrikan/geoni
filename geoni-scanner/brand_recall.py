"""
GEONI Scanner - Brand Recall Check
Queries OpenAI, Gemini, and Claude in parallel to check whether a
person/brand is recognized across AI models. The consensus result
(how many models recognize them) produces a more reliable signal
than any single model alone.

Score mapping:
  3/3 models recognize → 100 (High visibility)
  2/3 models recognize →  65 (Medium visibility)
  1/3 models recognize →  33 (Low visibility)
  0/3 models recognize →   0 (Not recognized)
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

SYSTEM_PROMPT = (
    "Sen yardımcı bir yapay zeka asistanısın. Kullanıcı sana bir alan veya konu "
    "hakkında sorduğunda, o alanda Türkiye'de bilinen veya öne çıkan gerçek "
    "kişileri, şirketleri ya da kurumları listele. Mutlaka somut isimler ver — "
    "\"bilmiyorum\" veya \"bu alanda kimse yok\" deme, o alanda kim varsa onu yaz. "
    "Her satırda \"• [İsim/Kurum] — [ne yaptığı veya bu alandaki rolü]\" formatında "
    "yaz. 4-6 isim sun. Markdown kullanma. Türkçe yanıtla."
)


def _normalize(text: str) -> str:
    text = text.strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text)


def _name_in_text(name: str, text: str) -> bool:
    norm_name = _normalize(name)
    norm_text = _normalize(text)
    if norm_name in norm_text:
        return True
    tokens = [t for t in norm_name.split() if len(t) > 2]
    if len(tokens) >= 2:
        return sum(1 for t in tokens if t in norm_text) >= 2
    return False


def _direct_recognized(response: str) -> bool:
    n = _normalize(response)
    return "taniniyor: evet" in n or "tanınıyor: evet" in n


# ── Model-specific query functions ──────────────────────────────────────────

async def _ask_claude(prompt: str, system: str = "", max_tokens: int = 400) -> str | None:
    if not ANTHROPIC_API_KEY:
        return None
    try:
        payload = {
            "model": "claude-sonnet-4-6",
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system
        async with httpx.AsyncClient() as c:
            r = await c.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json=payload, timeout=25,
            )
            if r.status_code == 200:
                blocks = r.json().get("content", [])
                return "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
            logger.warning(f"Claude {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.warning(f"Claude query failed: {e}")
    return None


async def _ask_openai(prompt: str, system: str = "", max_tokens: int = 400) -> str | None:
    if not OPENAI_API_KEY:
        return None
    try:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        async with httpx.AsyncClient() as c:
            r = await c.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                json={"model": "gpt-4o-mini", "messages": messages, "max_tokens": max_tokens, "temperature": 0.3},
                timeout=25,
            )
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
            logger.warning(f"OpenAI {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.warning(f"OpenAI query failed: {e}")
    return None


async def _ask_gemini(prompt: str, system: str = "", max_tokens: int = 400) -> str | None:
    if not GOOGLE_API_KEY:
        return None
    try:
        contents = []
        if system:
            contents += [
                {"role": "user", "parts": [{"text": system}]},
                {"role": "model", "parts": [{"text": "Anlaşıldı."}]},
            ]
        contents.append({"role": "user", "parts": [{"text": prompt}]})
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GOOGLE_API_KEY}",
                json={"contents": contents, "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.3}},
                timeout=25,
            )
            if r.status_code == 200:
                parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                return " ".join(p.get("text", "") for p in parts).strip()
            logger.warning(f"Gemini {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.warning(f"Gemini query failed: {e}")
    return None


# ── Per-model recognition check ─────────────────────────────────────────────

async def _check_single_model(name: str, topic: str, ask_fn) -> dict:
    """
    Run list-based + direct query for one model. Returns:
      {"recognized": bool, "raw": str | None}
    """
    use_direct = not topic.strip() or topic.strip().lower() == name.strip().lower()
    raw = None
    recognized = False

    if not use_direct:
        list_q = f"{topic} alanında Türkiye'de öne çıkan kişiler ve firmalar kimlerdir? Gerçek isimler ver."
        raw = await ask_fn(list_q, system=SYSTEM_PROMPT, max_tokens=400)
        if raw:
            recognized = _name_in_text(name, raw)

    if not recognized:
        ctx = f" ({topic} alanında)" if topic and not use_direct else ""
        direct_q = (
            f"Türkiye'de '{name}' adlı kişi veya kurum{ctx} hakkında ne biliyorsun? "
            f"Bu kişi/kurum Türkiye'de tanınan, bilinen biri mi?\n"
            f"Cevabını şu formatta ver:\n"
            f"TANINIYOR: evet veya hayır\n"
            f"AÇIKLAMA: [kısa açıklama, 1-2 cümle, Türkçe]"
        )
        direct_raw = await ask_fn(direct_q, max_tokens=200)
        if direct_raw:
            if _direct_recognized(direct_raw):
                recognized = True
                raw = (raw + "\n\n" + direct_raw) if raw else direct_raw

    return {"recognized": recognized, "raw": raw}


# ── Public API ───────────────────────────────────────────────────────────────

async def check_brand_recall(name: str, topic: str) -> dict:
    """
    Query all three models in parallel, return consensus result.
    """
    if not any([ANTHROPIC_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY]):
        return {"recognized": False, "score": None, "topic": topic, "raw_list": None,
                "checked": False, "model_results": {}}

    claude_task  = asyncio.create_task(_check_single_model(name, topic, _ask_claude))
    openai_task  = asyncio.create_task(_check_single_model(name, topic, _ask_openai))
    gemini_task  = asyncio.create_task(_check_single_model(name, topic, _ask_gemini))

    claude_res, openai_res, gemini_res = await asyncio.gather(
        claude_task, openai_task, gemini_task, return_exceptions=True
    )

    def safe(res):
        if isinstance(res, Exception):
            return {"recognized": False, "raw": None}
        return res

    claude_res  = safe(claude_res)
    openai_res  = safe(openai_res)
    gemini_res  = safe(gemini_res)

    model_results = {
        "claude":  {"recognized": claude_res["recognized"],  "model": "Claude"},
        "openai":  {"recognized": openai_res["recognized"],  "model": "ChatGPT"},
        "gemini":  {"recognized": gemini_res["recognized"],  "model": "Gemini"},
    }

    recognition_count = sum(1 for v in model_results.values() if v["recognized"])
    recognized = recognition_count > 0
    score = SCORE_MAP.get(recognition_count, 0)

    # Combine raw outputs for display
    raw_parts = []
    for key, res in [("claude", claude_res), ("openai", openai_res), ("gemini", gemini_res)]:
        if res.get("raw"):
            label = model_results[key]["model"]
            raw_parts.append(f"[{label}]\n{res['raw']}")
    raw_list = "\n\n".join(raw_parts) if raw_parts else None

    logger.info(f"Brand recall for '{name}': {recognition_count}/3 models recognized ({score} pts)")

    return {
        "recognized": recognized,
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
        raw = await _ask_claude(prompt, max_tokens=100) or \
              await _ask_openai(prompt, max_tokens=100) or \
              await _ask_gemini(prompt, max_tokens=100)
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
