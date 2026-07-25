"""
GEONI - Expo push bildirimleri.

Izleme (monitor) taramasinda skor anlamli degistiginde kullanicinin
mobil cihazlarina push gonderir. Token'lar push_tokens tablosunda
(mobil uygulama kaydeder). Fail-silent.
"""
import os
import logging

import httpx

logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


def _headers() -> dict:
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }


async def _get_tokens(user_id: str) -> list[str]:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not user_id:
        return []
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/push_tokens?user_id=eq.{user_id}&select=token",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200:
                return [row["token"] for row in r.json() if row.get("token")]
    except Exception as e:
        logger.warning(f"push token fetch error: {e}")
    return []


async def send_new_task_push(expert_id: str, task_name: str, ref_code: str = "") -> None:
    """Uzmanlik alani eslesen uzmana yeni gorev bildirimi. Uzman uygulamayi
    acip gorevi ustlenebilir. Token yoksa (uzman giris yapmamis/cihaz yok)
    sessizce gecer."""
    tokens = await _get_tokens(expert_id)
    if not tokens:
        return
    ref = f" ({ref_code})" if ref_code else ""
    messages = [
        {
            "to": t,
            "sound": "default",
            "title": "Yeni görev müsait 🎯",
            "body": f"{task_name}{ref} — uygulamayı açıp üstlenebilirsin.",
            "data": {"type": "new_task", "ref_code": ref_code},
        }
        for t in tokens
    ]
    try:
        async with httpx.AsyncClient() as client:
            await client.post(EXPO_PUSH_URL, json=messages, headers={"Content-Type": "application/json"}, timeout=15)
        logger.info(f"new-task push sent to {len(tokens)} device(s) for expert {expert_id}")
    except Exception as e:
        logger.warning(f"expo new-task push error: {e}")


async def send_referral_reward_push(referrer_id: str, credits: int) -> None:
    """Davet ettigi kisi ilk taramasini tamamlayinca davet EDENe bildirim.

    NEDEN sadece davet edene: davetli odulu zaten uygulamanin icinde, o an,
    bakiyesinde gorunur. Davet eden ise olayi hic gormez — bildirim olmazsa
    "davet ettim, bir sey olmadi" sanir ve dongu bir kez donup durur. Viral
    dongunun geri besleme halkasi budur.

    Bu bir ISLEM bildirimi (hesabina kontor gecti), pazarlama degil — Apple
    4.5.4 anlaminda guvenli. "Arkadasini davet et" hatirlatmasi ASLA push ile
    gonderilmez; o uygulama ici davet kartinda durur.
    """
    tokens = await _get_tokens(referrer_id)
    if not tokens:
        return
    messages = [
        {
            "to": t,
            "sound": "default",
            "title": "Davetin karşılık buldu 🎉",
            "body": f"Davet ettiğin kişi ilk taramasını yaptı — hesabına +{credits} token geçti.",
            "data": {"type": "referral_reward", "credits": credits},
        }
        for t in tokens
    ]
    try:
        async with httpx.AsyncClient() as client:
            await client.post(EXPO_PUSH_URL, json=messages, headers={"Content-Type": "application/json"}, timeout=15)
        logger.info(f"referral-reward push sent to {len(tokens)} device(s) for {referrer_id}")
    except Exception as e:
        logger.warning(f"expo referral push error: {e}")


async def send_score_change_push(user_id: str, label: str, old_score: int, new_score: int) -> None:
    """Izleme skoru degisiminde push gonderir. Kullanicinin kayitli tum
    cihazlarina; token yoksa sessizce gecer."""
    tokens = await _get_tokens(user_id)
    if not tokens:
        return
    delta = new_score - old_score
    yon = "yükseldi" if delta > 0 else "düştü"
    arrow = "▲" if delta > 0 else "▼"
    messages = [
        {
            "to": t,
            "sound": "default",
            "title": f"{label} skoru {yon} {arrow}",
            "body": f"AI görünürlük skoru {old_score} → {new_score} ({arrow}{abs(delta)} puan).",
            "data": {"type": "score_change", "label": label},
        }
        for t in tokens
    ]
    try:
        async with httpx.AsyncClient() as client:
            await client.post(EXPO_PUSH_URL, json=messages, headers={"Content-Type": "application/json"}, timeout=15)
        logger.info(f"push sent to {len(tokens)} device(s) for '{label}'")
    except Exception as e:
        logger.warning(f"expo push send error: {e}")
