"""
GEONI - Grok (xAI) self-estimated cost tracking.

xAI has no spend/usage API GEONI can poll, so cost is ESTIMATED from the
provider_usage call counts GEONI already logs (log_provider_call "grok" /
"grok_web") multiplied by a per-call price. Two call classes:

- "grok"     = parametric recall SHADOW engine (brand_recall._ask_grok). Cheap:
               grok-4.20 non-reasoning, ~1.5k in + 0.5k out ≈ $0.0007/call.
- "grok_web" = Agent Tools API SOV SHADOW engine (_ask_grok_web, web+x search).
               2026-08-03 OLCULDU: eski "~20k token" varsayimi YANLISTI — grok-4.5
               gercekte 49k-173k girdi tokeni yakiyordu (3 istem, ort ~125k), yani
               bu satirdaki tahmin ~6x DUSUK hesapliyordu. Model grok-4.3'e alindi
               (%82-90 ucuz, atif denk); yeni gercekci aralik ~12k-34k girdi.

Same summary shape as perplexity_admin/openai_admin so the admin panel reuses
the same stat-tile + TopupSection + low-balance layout. `estimated: True`.
"""

from datetime import datetime, timedelta, timezone

from db import get_grok_usage_daily

# Cagri-basi tahmini USD maliyet. GERCEK KONSOL VERISIYLE KALIBRE (2026-07-23):
# ilk deneylerde toplam $0.67 -> web arama 45×~$0.005 + X arama 22×~$0.005 + prompt
# token 127.8K@grok-4.5 ($0.25) -> ~8-10 grok-web cagri => ~$0.08/cagri (web+x+token dahil).
# Parametrik grok (31 cagri) ihmal edilebilir kaldi. Flat oran; token/arama bazli degil
# (provider_usage yalniz cagri sayisi tutuyor, sema degistirmeden en iyi tahmin).
GROK_CALL_COST = 0.0007      # parametrik recall (ucuz, hizli; konsolda ihmal edilebilir cikti)
# grok-web GERCEK ~$0.08/cagri ama degisken (model kac arama turu yapacagina kendi karar verir,
# gercek kullanim loglanmiyor). Fable notu: dusuk tahmin -> bakiye uyarisi GEC tetiklenir.
# Bu yuzden ~%25 GUVENLIK PAYI ile ust-tahmin: harcama biraz yuksek gorunur -> alarm ERKEN (guvenli).
GROK_WEB_CALL_COST = 0.10    # web+x search, guvenlik payli (gercek ~$0.08; geç-alarm riskine karsi)

ALL_TIME_LOOKBACK_DAYS = 365  # perplexity_admin ile ayni pencere (dusuk-bakiye false-negative daraltmasi)


def _day_cost(counts: dict) -> float:
    return (counts.get("grok", 0) * GROK_CALL_COST
            + counts.get("grok_web", 0) * GROK_WEB_CALL_COST)


async def _daily_cost(start: datetime, end: datetime) -> dict[str, float]:
    usage = await get_grok_usage_daily(start, end)   # {date: {grok, grok_web}}
    return {d: _day_cost(c) for d, c in usage.items()}


async def get_grok_cost_summary() -> dict:
    """usd_today/week/month/all_time/daily — perplexity_admin ile ayni sekil."""
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    month_daily = await _daily_cost(month_start, now)
    all_time_daily = await _daily_cost(now - timedelta(days=ALL_TIME_LOOKBACK_DAYS), now)
    usd_all_time = sum(all_time_daily.values())

    today_key = now.strftime("%Y-%m-%d")
    week_start_key = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    # Fable notu: today/week penceresi ay-sinirindan ETKILENMEMELI (ayin 1-2'sinde
    # son 7 gun onceki aya taşar). all_time_daily tam pencereyi kapsar -> kullan.
    usd_today = all_time_daily.get(today_key, 0)
    usd_week = sum(v for k, v in all_time_daily.items() if k >= week_start_key)
    usd_month = sum(month_daily.values())

    return {
        "usd_today": round(usd_today, 4),
        "usd_week": round(usd_week, 4),
        "usd_month": round(usd_month, 4),
        "usd_all_time": round(usd_all_time, 4),
        "daily": [{"date": d, "usd": round(v, 4)} for d, v in sorted(month_daily.items())],
        "estimated": True,
        "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


async def get_grok_monthly_breakdown() -> dict[str, float]:
    """USD maliyet ay bazinda (YYYY-MM) — total_cost_admin icin."""
    now = datetime.now(timezone.utc)
    daily = await _daily_cost(now - timedelta(days=ALL_TIME_LOOKBACK_DAYS), now)
    monthly: dict[str, float] = {}
    for date_key, amount in daily.items():
        mk = date_key[:7]
        monthly[mk] = monthly.get(mk, 0) + amount
    return {k: round(v, 4) for k, v in monthly.items()}
