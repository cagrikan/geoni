"""Oz-gelisim motoru — taramalardan (audits.result_json) sinyal turetir.

Iki iz, tek gecis:
  IS/GORUNURLUK
    A. own_visibility  — AI cevaplarinda 'geoni'/'geoni.ai' geciyor mu (motor bazli)
    B. niche_pain      — nis basina ort skor (dusuk = aci = satis) + domine rakipler
    C. content_gap     — en cok tekrarlayan gercek sorular (rehber konusu)
  KALITE (daha iyi arama sonucu)
    Q. quality_engine  — motor guvenilirligi (cevaplama/kaynak orani)
       quality_overall — sorgu cevaplanma orani, skor kararlilik, grounding

Sinyalleri improvement_signals'e yazar, digest doner. RISKLI hicbir sey otomatik
degistirmez — sadece olcer ve raporlar; prompt/agirlik degisimi admin onayina duser.
"""
import re
import asyncio
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta

import httpx
from db import SUPABASE_URL, SUPABASE_SERVICE_KEY, _headers

logger = logging.getLogger("self_improve")
_GEONI = re.compile(r"geoni", re.I)


async def improvement_loop():
    """Always-on: gunde bir kez oz-gelisim dongusunu calistirir (harvest+analyze+
    yaz). Riskli hicbir sey otomatik degistirmez; sadece sinyal uretir."""
    await asyncio.sleep(300)  # servis otursun
    while True:
        try:
            d = await run_improvement_cycle(days=7)
            logger.info(f"improvement_loop ran: {d.get('signals_written')} signals")
        except Exception as e:
            logger.warning(f"improvement_loop error: {e}")
        await asyncio.sleep(24 * 3600)  # gunluk


async def run_improvement_cycle(days: int = 7, top_n: int = 25) -> dict:
    """Son `days` gunun brand/person/social taramalarini hasat eder, A/B/C/Q
    sinyallerini hesaplar, improvement_signals'e (bugun icin) yazar ve digest doner."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"ok": False, "error": "not_configured"}
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/audits?select=type,result_json,created_at"
                f"&status=eq.complete&created_at=gte.{since}"
                f"&type=in.(brand,person,social)&order=created_at.desc&limit=1500",
                headers=_headers(), timeout=40,
            )
            rows = r.json() if r.status_code == 200 else []
    except Exception as e:
        logger.warning(f"harvest fetch error: {e}")
        return {"ok": False, "error": "fetch_failed"}

    query_freq = Counter()                    # C
    own_mention = defaultdict(int)            # A: engine -> 'geoni' gecen cevap sayisi
    eng_answered = defaultdict(int)           # Q/A denom: engine -> answered
    eng_hassrc = defaultdict(int)             # Q: engine -> kaynakli cevap
    niche_scores = defaultdict(list)          # B: topic -> [score]
    competitor_freq = defaultdict(Counter)    # B: topic -> Counter(rakip)
    stabilities = []                          # Q4
    q_total = 0                               # Q1 denom (sorgu-motor cifti)
    q_answered = 0                            # Q1: cevaplanan
    ungrounded = 0                            # Q5: mentioned=true ama kaynak yok
    scanned = 0

    for a in rows:
        rj = a.get("result_json") or {}
        if not isinstance(rj, dict):
            continue
        scanned += 1
        sov = rj.get("sov") or {}
        topic = str(rj.get("topic") or "").strip().lower()
        score = rj.get("score")
        if isinstance(score, (int, float)) and topic:
            niche_scores[topic].append(float(score))
        stab = rj.get("stability")
        if isinstance(stab, (int, float)):
            stabilities.append(float(stab))
        for comp in (sov.get("competitors") or []):
            nm = comp.get("name") if isinstance(comp, dict) else comp
            if nm and topic:
                competitor_freq[topic][str(nm).strip()] += 1
        for q in (sov.get("queries") or []):
            if not isinstance(q, dict):
                continue
            qt = str(q.get("query") or "").strip()
            if qt:
                query_freq[qt] += 1
            snippet = str(q.get("answer_snippet") or "")
            for eng, ed in (q.get("engines") or {}).items():
                if not isinstance(ed, dict):
                    continue
                q_total += 1
                answered = bool(ed.get("answered"))
                srcs = ed.get("sources") or []
                if answered:
                    q_answered += 1
                    eng_answered[eng] += 1
                if srcs:
                    eng_hassrc[eng] += 1
                if _GEONI.search(snippet + " " + " ".join(str(s) for s in srcs)):
                    own_mention[eng] += 1
                if ed.get("mentioned") and not srcs:
                    ungrounded += 1

    # ---- Sinyalleri kur ----
    signals = []
    engines = set(eng_answered) | set(own_mention) | set(eng_hassrc)
    for eng in engines:
        ans = eng_answered.get(eng, 0)
        signals.append({"kind": "own_visibility", "subject": eng,
                        "metric": own_mention.get(eng, 0),
                        "detail": {"answered": ans,
                                   "mention_rate": round(own_mention.get(eng, 0) / ans, 3) if ans else 0}})
        signals.append({"kind": "quality_engine", "subject": eng,
                        "metric": ans,
                        "detail": {"has_sources": eng_hassrc.get(eng, 0),
                                   "source_rate": round(eng_hassrc.get(eng, 0) / ans, 3) if ans else 0}})

    for qt, freq in query_freq.most_common(top_n):
        signals.append({"kind": "content_gap", "subject": qt[:400], "metric": freq, "detail": None})

    for topic, scores in niche_scores.items():
        if len(scores) < 1:
            continue
        top_comp = [c for c, _ in competitor_freq.get(topic, Counter()).most_common(5)]
        signals.append({"kind": "niche_pain", "subject": topic[:200],
                        "metric": round(sum(scores) / len(scores), 1),
                        "detail": {"scans": len(scores), "top_competitors": top_comp}})

    avg_stab = round(sum(stabilities) / len(stabilities), 1) if stabilities else None
    signals.append({"kind": "quality_overall", "subject": "answer_rate",
                    "metric": round(q_answered / q_total, 3) if q_total else 0,
                    "detail": {"query_engine_pairs": q_total}})
    signals.append({"kind": "quality_overall", "subject": "score_stability",
                    "metric": avg_stab, "detail": {"n": len(stabilities)}})
    signals.append({"kind": "quality_overall", "subject": "ungrounded_mentions",
                    "metric": ungrounded, "detail": None})

    # ---- Yaz (bugun icin idempotent: once bugunku sil) ----
    written = 0
    try:
        async with httpx.AsyncClient() as client:
            today = datetime.now(timezone.utc).date().isoformat()
            await client.delete(
                f"{SUPABASE_URL}/rest/v1/improvement_signals?cycle_date=eq.{today}",
                headers=_headers(), timeout=15)
            if signals:
                rows_out = [{**s, "cycle_date": today} for s in signals]
                w = await client.post(
                    f"{SUPABASE_URL}/rest/v1/improvement_signals",
                    headers=_headers(), json=rows_out, timeout=20)
                written = len(rows_out) if w.status_code in (200, 201) else 0
    except Exception as e:
        logger.warning(f"signal write error: {e}")

    digest = {
        "ok": True,
        "scanned_audits": scanned,
        "own_visibility": {e: own_mention.get(e, 0) for e in engines},
        "top_questions": [q for q, _ in query_freq.most_common(10)],
        "painful_niches": sorted(
            ({"topic": t, "avg_score": round(sum(s) / len(s), 1), "scans": len(s)}
             for t, s in niche_scores.items()),
            key=lambda x: x["avg_score"])[:10],
        "quality": {"answer_rate": round(q_answered / q_total, 3) if q_total else 0,
                    "avg_stability": avg_stab, "ungrounded_mentions": ungrounded},
        "signals_written": written,
    }
    logger.info(f"improvement cycle: {scanned} audits, {written} signals")
    return digest


async def get_signals(cycle_date: str | None = None) -> dict:
    """Admin okuma: en son (veya verilen) donemin sinyallerini kind bazli grupla."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"cycle_date": None, "signals": {}}
    try:
        async with httpx.AsyncClient() as client:
            if not cycle_date:
                lr = await client.get(
                    f"{SUPABASE_URL}/rest/v1/improvement_signals?select=cycle_date&order=cycle_date.desc&limit=1",
                    headers=_headers(), timeout=10)
                j = lr.json() if lr.status_code == 200 else []
                cycle_date = j[0]["cycle_date"] if j else None
            if not cycle_date:
                return {"cycle_date": None, "signals": {}}
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/improvement_signals?cycle_date=eq.{cycle_date}"
                f"&select=kind,subject,metric,detail&order=metric.desc.nullslast",
                headers=_headers(), timeout=15)
            rows = r.json() if r.status_code == 200 else []
    except Exception as e:
        logger.warning(f"get_signals error: {e}")
        return {"cycle_date": cycle_date, "signals": {}}
    grouped: dict[str, list] = {}
    for row in rows:
        grouped.setdefault(row["kind"], []).append(row)
    return {"cycle_date": cycle_date, "signals": grouped}
