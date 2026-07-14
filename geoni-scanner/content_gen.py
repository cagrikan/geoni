"""
GEONI — Haftalık içerik otomasyonu (always-on scanner içinde).

Sıfır-kitle büyümenin GÜVENLİ otomasyon parçası: ürünün kendi (anonim, toplam)
tarama verisinden her hafta sosyal içerik üretir ve KURUCUYA e-postalar. Böylece
"ne yazacağım" işi ortadan kalkar; yayın kararı insanda kalır (biz post ATMAYIZ,
DM ATMAYIZ — ban/KVKK riski). Reklam ve soğuk erişim bilinçli olarak dışarıda.

Cadence: haftada bir (varsayılan Pazartesi). Restart-safe: gönderilen ISO hafta
app_config'te tutulur, yeniden başlatma çift göndermez.

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, ANTHROPIC_API_KEY (scanner'da mevcut).
     GEONI_CONTENT_EMAIL (varsayılan mail@geoni.ai), CONTENT_WEEKDAY (0=Pzt).
"""
import asyncio
import logging
import os
from datetime import datetime, timezone

import httpx

from mailer import send_ticket_email

logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TO_EMAIL = os.environ.get("GEONI_CONTENT_EMAIL", "mail@geoni.ai")
WEEKDAY = int(os.environ.get("CONTENT_WEEKDAY", "0"))       # 0=Pazartesi
CHECK_HOURS = int(os.environ.get("CONTENT_CHECK_HOURS", "6"))

SYSTEM = """Sen GEONI'nin içerik üreticisisin. GEONI markaların/kişilerin/sitelerin
ChatGPT, Gemini, Perplexity, Claude cevaplarındaki görünürlüğünü ölçer ve iyileştirir.
Çekirdek mesaj: "AI seni tanıyor mu?". Kategori: GEO/AEO (AI aramada görünürlük).
Sana GERÇEK haftalık tarama istatistikleri verilecek; bunlardan 1 haftalık içerik üret.
KURALLAR: Ton alçakgönüllü, veri konuşur, şov yok. Türkçe, kusursuz dilbilgisi.
LinkedIn: 3-6 kısa paragraf, tek CTA (geoni.ai), 3-4 hashtag. Instagram: KISA,
URL YOK (önizleme balonu) — yönlendirme "App Store'da GEONI". Rakamları çarpıcı ama
dürüst çerçevele, uydurma istatistik yok. Emoji ölçülü. Çıktı: [LINKEDIN-1] ...
[LINKEDIN-2] ... [INSTAGRAM] ... [IG-CAPTION-TEK-SATIR] ..."""


def _h():
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json"}


async def _fetch_stats(client: httpx.AsyncClient) -> dict:
    r = await client.get(
        f"{SUPABASE_URL}/rest/v1/audits?status=eq.complete&select=type,score,created_at"
        f"&order=created_at.desc&limit=1000", headers=_h(), timeout=20)
    rows = r.json() if r.status_code == 200 else []
    sc = [x["score"] for x in rows if isinstance(x.get("score"), (int, float))]
    web = [x["score"] for x in rows if x.get("type") == "web" and isinstance(x.get("score"), (int, float))]
    brand = [x["score"] for x in rows if x.get("type") in ("person", "brand") and isinstance(x.get("score"), (int, float))]
    from datetime import timedelta
    wk = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    return {"toplam": len(rows), "son7g": sum(1 for x in rows if x.get("created_at", "") >= wk),
            "ort_web": round(sum(web) / len(web)) if web else None,
            "ort_marka": round(sum(brand) / len(brand)) if brand else None,
            "dusuk": sum(1 for s in sc if s < 40), "yuksek": sum(1 for s in sc if s >= 70)}


async def _generate(client: httpx.AsyncClient, stats: dict) -> str | None:
    if not ANTHROPIC_KEY:
        return None
    prompt = (f"Bu haftanın gerçek verisi: toplam {stats['toplam']} tarama, son 7 günde "
              f"{stats['son7g']}. Ortalama skorlar — web: {stats['ort_web']}/100, marka/kişi: "
              f"{stats['ort_marka']}/100. {stats['dusuk']} tarama 40 altında, {stats['yuksek']} "
              f"tanesi 70+. Bu verilerden bu haftanın içeriğini üret.")
    r = await client.post("https://api.anthropic.com/v1/messages",
                          headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
                                   "content-type": "application/json"},
                          json={"model": "claude-sonnet-5", "max_tokens": 1500,
                                "system": SYSTEM, "messages": [{"role": "user", "content": prompt}]},
                          timeout=90)
    if r.status_code != 200:
        logger.warning("content_gen: anthropic %s", r.status_code)
        return None
    d = r.json()
    return "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text").strip() or None


async def _last_sent_week(client: httpx.AsyncClient) -> str:
    try:
        r = await client.get(f"{SUPABASE_URL}/rest/v1/app_config?key=eq.content_last_week&select=value",
                             headers=_h(), timeout=10)
        rows = r.json() if r.status_code == 200 else []
        return rows[0]["value"] if rows else ""
    except Exception:
        return ""


async def _mark_week(client: httpx.AsyncClient, week: str):
    try:
        await client.post(f"{SUPABASE_URL}/rest/v1/app_config?on_conflict=key",
                          headers={**_h(), "Prefer": "resolution=merge-duplicates"},
                          json={"key": "content_last_week", "value": week}, timeout=10)
    except Exception as e:
        logger.warning("content_gen: mark_week %s", e)


async def content_loop():
    """Haftalık içerik döngüsü. monitor_loop gibi startup'ta başlatılır."""
    await asyncio.sleep(180)  # servis otursun
    while True:
        try:
            now = datetime.now(timezone.utc)
            week = now.strftime("%G-W%V")
            if now.weekday() == WEEKDAY and SUPABASE_URL and SUPABASE_KEY:
                async with httpx.AsyncClient() as client:
                    if await _last_sent_week(client) != week:
                        stats = await _fetch_stats(client)
                        content = await _generate(client, stats)
                        if content:
                            lines = [f"Bu haftanın verisi: {stats}", "", content, "",
                                     "— Otomatik üretildi (content_gen). Yayın senin kararın; "
                                     "IG'de URL yok kuralı geçerli."]
                            await send_ticket_email(TO_EMAIL, f"GEONI haftalık içerik · {week}",
                                                    "Bu haftanın hazır içeriği", lines,
                                                    cta_label="Uygulamayı Aç", cta_url="https://app.geoni.ai")
                            await _mark_week(client, week)
                            logger.info("content_gen: %s içeriği %s adresine gönderildi", week, TO_EMAIL)
        except Exception as e:
            logger.warning("content_loop hata: %s", e)
        await asyncio.sleep(CHECK_HOURS * 3600)
