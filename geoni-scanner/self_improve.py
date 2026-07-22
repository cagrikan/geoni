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
import os
import re
import asyncio
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta

import httpx
from db import SUPABASE_URL, SUPABASE_SERVICE_KEY, _headers, _claim_daily_job

logger = logging.getLogger("self_improve")
_GEONI = re.compile(r"geoni", re.I)


SELF_SCAN_DOMAIN = "geoni.ai"
# Otonom bilgilendirme: haftalik kalite ozeti buraya gider (content_gen ile ayni kutu).
FOUNDER_EMAIL = os.environ.get("GEONI_CONTENT_EMAIL", "mail@geoni.ai")


def _quality_digest_lines(digest: dict, m_total, m_recog, m_hallu,
                          shadow_deltas, shadow_tail) -> list:
    """Kurucuya gidecek KALITE ozeti (amac: arama motoru kalitesini yukseltmek —
    kalite sinyalleri gozle gorunur olsun). run_improvement_cycle'in locallerinden
    beslenir; salt-okuma, hicbir sey degistirmez."""
    q = digest.get("quality", {}) or {}
    ar = q.get("answer_rate") or 0
    lines = [
        f"Taranan tarama (7g): {digest.get('scanned_audits', 0)}",
        f"Sorgu cevap oranı: %{round(ar * 100)} · skor kararlılık ±{q.get('avg_stability')} · "
        f"kaynaksız cevap: {q.get('answers_no_source')}",
        f"Sorgu kalitesi: konudan-kayma %{round((q.get('adjacent_rate') or 0) * 100)} · "
        f"ölü sorgu %{round((q.get('dead_query_rate') or 0) * 100)}",
    ]
    if m_total:
        lines.append("Motor tanınma: " + ", ".join(
            f"{e} %{round((m_recog.get(e, 0) / m_total[e]) * 100) if m_total[e] else 0}"
            for e in sorted(m_total)))
        worst = max(((e, (m_hallu.get(e, 0) / m_total[e]) if m_total[e] else 0) for e in m_total),
                    key=lambda x: x[1])
        lines.append(f"En yüksek halüsinasyon: {worst[0]} %{round(worst[1] * 100)}")
    if shadow_deltas:
        n = len(shadow_deltas)
        tr = shadow_tail / n
        lines.append(f"Skor sapması (v4↔gölge): kuyruk %{round(tr * 100)} ({shadow_tail}/{n}), "
                     f"ort {round(sum(shadow_deltas) / n, 1)}")
        if tr > 0.15:
            lines.append("⚠️ Skor sapması yüksek — v4→gölge faz-2 geçişi değerlendirilmeli (Kritik Karar #1).")
    pn = (digest.get("painful_niches") or [])[:3]
    if pn:
        lines.append("En düşük skorlu nişler: " + ", ".join(f"{p['topic']} ({p['avg_score']})" for p in pn))
    tq = (digest.get("top_questions") or [])[:3]
    if tq:
        lines.append("En sık sorular: " + " · ".join(tq))
    return lines


async def self_scan() -> int | None:
    """geoni.ai'yi tarar (kendi AI-gorunurluk skor trendimiz icin) ve audits'e
    yazar. monitor._scan_web_item'i tekrar kullanir; haftada bir yeter."""
    try:
        from monitor import _scan_web_item
        score = await _scan_web_item({"target": {"domain": SELF_SCAN_DOMAIN}})
        logger.info(f"self-scan {SELF_SCAN_DOMAIN}: score={score}")
        return score
    except Exception as e:
        logger.warning(f"self_scan error: {e}")
        return None


async def improvement_loop():
    """Always-on: gunde bir kez oz-gelisim dongusunu calistirir (harvest+analyze+
    yaz) + haftada bir geoni.ai self-scan. Riskli hicbir sey otomatik degistirmez;
    sadece sinyal/olcum uretir."""
    await asyncio.sleep(300)  # servis otursun
    while True:
        try:
            # Coklu-instance guvenli: yalnizca gunluk kilidi ALAN instance calisir
            # (autoscale'de cift self_scan maliyeti + improvement_signals delete/insert
            # yarisi olmaz — retention isindeki _claim_daily_job deseni).
            if await _claim_daily_job("improvement"):
                # Pazartesi self-scan'i cycle'dan ONCE calissin ki taze geoni.ai
                # own_recognition AYNI gun sinyaline girsin (eskiden 1 gun gecikiyordu).
                is_monday = datetime.now(timezone.utc).weekday() == 0
                if is_monday:
                    await self_scan()
                # Haftalik (Pazartesi) kurucuya otonom kalite ozeti maili (notify).
                d = await run_improvement_cycle(days=7, notify=is_monday)
                logger.info(f"improvement_loop ran: {d.get('signals_written')} signals")
            else:
                logger.info("improvement_loop: bugun baska instance calisti, atlandi")
        except Exception as e:
            logger.warning(f"improvement_loop error: {e}")
        await asyncio.sleep(24 * 3600)  # gunluk


async def run_improvement_cycle(days: int = 7, top_n: int = 25, notify: bool = False) -> dict:
    """Son `days` gunun brand/person/social taramalarini hasat eder, A/B/C/Q
    sinyallerini hesaplar, improvement_signals'e (bugun icin) yazar ve digest doner."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"ok": False, "error": "not_configured"}
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    geoni_mr = {}
    try:
        async with httpx.AsyncClient() as client:
            # Sayfalama: PostgREST ~1000 satir cap'ine takilmadan 7 gunluk pencerenin
            # TAMAMINI cek (buyumede en eski kayitlarin sessizce dusmesini onler).
            rows = []
            offset, PAGE = 0, 1000
            while True:
                r = await client.get(
                    f"{SUPABASE_URL}/rest/v1/audits?select=type,result_json,created_at"
                    f"&status=eq.complete&created_at=gte.{since}"
                    f"&type=in.(brand,person,social)&order=created_at.desc"
                    f"&limit={PAGE}&offset={offset}",
                    headers=_headers(), timeout=40,
                )
                batch = r.json() if r.status_code == 200 else []
                rows.extend(batch)
                if len(batch) < PAGE or offset >= 50000:  # tavan: patolojik durumda sonsuz donmesin
                    break
                offset += PAGE
            # geoni.ai self-scan (own_recognition): SADECE haftalik self_scan kaydini al
            # (auto_monitor=true). Aksi halde araya giren normal kullanici web-taramasi
            # "en son" gelir; onun model_results'i brand_recall altinda ic-ice oldugundan
            # top-level okuma bosalir ve sinyal SESSIZCE yanlis/bos uretilir.
            gm = await client.get(
                f"{SUPABASE_URL}/rest/v1/audits?type=eq.web&domain=eq.geoni.ai"
                f"&status=eq.complete&result_json->>auto_monitor=eq.true"
                f"&select=result_json&order=created_at.desc&limit=1",
                headers=_headers(), timeout=15,
            )
            g = gm.json() if gm.status_code == 200 else []
            if g:
                geoni_mr = (g[0].get("result_json") or {}).get("model_results") or {}
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
    no_source = 0                             # Q5: cevaplandi ama HIC kaynak yok
    q_count = 0                               # Faz1-1.2: toplam sorgu (query-motor cifti DEGIL)
    q_adjacent = 0                            # sorgu konudan kaydi (adjacent) — uretim kalitesi
    q_dead = 0                                # HIC motor cevaplamadi — zayif sorgu formulasyonu
    m_total = defaultdict(int)                # 4-motor (model_results: claude/gemini/openai/perplexity)
    m_recog = defaultdict(int)                # taninma
    m_acc_sum = defaultdict(float)            # dogruluk_skoru toplami
    m_acc_n = defaultdict(int)
    m_hallu = defaultdict(int)                # uydurma_suphesi
    m_contra = defaultdict(int)               # celiski_var
    shadow_deltas = []                        # Grup B: v4 score - score_shadow (flip karari)
    shadow_tail = 0                           # |delta| > 10 (KUYRUK — B7'nin etkiledigi uc vakalar)
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
        # Grup B flip-karari: v4 manset ile golge (B6+B7) skoru karsilastir.
        shadow = rj.get("score_shadow")
        if isinstance(score, (int, float)) and isinstance(shadow, (int, float)):
            d = float(score) - float(shadow)
            shadow_deltas.append(d)
            if abs(d) > 10:
                shadow_tail += 1
        stab = rj.get("stability")
        # stability bir obje: {delta, smoothed_score, prev_score, ...}. Oynaklik
        # olcusu = |delta| (skorun bir onceki taramaya gore ne kadar sicradigi).
        if isinstance(stab, dict) and isinstance(stab.get("delta"), (int, float)):
            stabilities.append(abs(float(stab["delta"])))
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
            q_count += 1
            if q.get("adjacent"):        # sorgu hedef konudan komsu konuya kaydi
                q_adjacent += 1
            _answered_any = False
            for eng, ed in (q.get("engines") or {}).items():
                if not isinstance(ed, dict):
                    continue
                q_total += 1
                srcs = ed.get("sources") or []
                if not ed.get("answered"):
                    continue
                _answered_any = True
                q_answered += 1
                eng_answered[eng] += 1
                # F2: geoni gecisini motor BASINA yalniz o motorun kaynaklarindan
                # say (motor-bazli, guvenilir) ve YALNIZ answered iken -> mention_rate
                # = own_mention/eng_answered artik 1'i (%100) asamaz. (Eski kod sorgu
                # seviyesindeki tek 'answer_snippet'i her motora sayip sisiriyordu.)
                if srcs:
                    eng_hassrc[eng] += 1
                    if _GEONI.search(" ".join(str(s) for s in srcs)):
                        own_mention[eng] += 1
                else:
                    no_source += 1
            if not _answered_any:   # hicbir motor cevaplamadi -> olu/zayif sorgu
                q_dead += 1
        # 4-MOTOR kalite (model_results): taninma + dogruluk + halusinasyon + celiski
        for meng, mv in (rj.get("model_results") or {}).items():
            if not isinstance(mv, dict):
                continue
            m_total[meng] += 1
            if mv.get("recognized"):
                m_recog[meng] += 1
            j = mv.get("judge") or {}
            acc = j.get("dogruluk_skoru")
            if isinstance(acc, (int, float)):
                m_acc_sum[meng] += acc
                m_acc_n[meng] += 1
            if j.get("uydurma_suphesi"):
                m_hallu[meng] += 1
            if j.get("celiski_var"):
                m_contra[meng] += 1

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

    # Faz0-0.2: competitor_sov — nis basina rakip PAYI (share of voice) AYRI sinyal.
    # Rakip verisi eskiden yalniz niche_pain.detail'a gomuluydu (zaman-serisi cikmaz);
    # kind bazli yazilinca hafta-hafta SOV-drift (kim yukseliyor/dusuyor) turetilebilir.
    for topic, ctr in competitor_freq.items():
        tot = sum(ctr.values())
        if tot < 1:
            continue
        for comp, cnt in ctr.most_common(5):
            signals.append({"kind": "competitor_sov",
                            "subject": f"{topic[:120]}|{comp[:120]}",
                            "metric": round(cnt / tot, 3),
                            "detail": {"topic": topic[:200], "competitor": comp[:200],
                                       "mentions": cnt, "of": tot}})

    # Q: 4-MOTOR kalite (claude/gemini/openai/perplexity) — model_results'tan
    for meng in m_total:
        tot = m_total[meng]
        signals.append({"kind": "quality_model", "subject": meng,
                        "metric": round(m_recog[meng] / tot, 3) if tot else 0,
                        "detail": {"scans": tot, "recognized": m_recog[meng],
                                   "accuracy_avg": round(m_acc_sum[meng] / m_acc_n[meng], 1) if m_acc_n[meng] else None,
                                   "hallucination_rate": round(m_hallu[meng] / tot, 3) if tot else 0,
                                   "contradiction_rate": round(m_contra[meng] / tot, 3) if tot else 0}})
    # A: geoni.ai self-scan'den 4-motor kendi taninma
    for meng, mv in (geoni_mr or {}).items():
        if isinstance(mv, dict):
            signals.append({"kind": "own_recognition", "subject": meng,
                            "metric": 1 if mv.get("recognized") else 0,
                            "detail": {"score": mv.get("score")}})

    avg_stab = round(sum(stabilities) / len(stabilities), 1) if stabilities else None
    signals.append({"kind": "quality_overall", "subject": "answer_rate",
                    "metric": round(q_answered / q_total, 3) if q_total else 0,
                    "detail": {"query_engine_pairs": q_total}})
    signals.append({"kind": "quality_overall", "subject": "score_stability",
                    "metric": avg_stab, "detail": {"n": len(stabilities)}})
    signals.append({"kind": "quality_overall", "subject": "ungrounded_mentions",
                    "metric": no_source, "detail": {"note": "cevaplandi ama kaynak yok"}})
    # Faz1-1.2: sorgu-uretim kalitesi (arama motoru kalitesi merkezi). adjacent_rate
    # = sorgu konudan kayma orani; dead_query_rate = hic motor cevaplamayan sorgu orani.
    # Yuksek deger -> generate_category_queries iyilestirilmeli (olcum otonom; sorgu
    # havuzunu degistirmek stability'yi bozabilecegi icin ONAYLI kalir).
    if q_count:
        signals.append({"kind": "query_quality", "subject": "adjacent_rate",
                        "metric": round(q_adjacent / q_count, 3),
                        "detail": {"queries": q_count, "adjacent": q_adjacent}})
        signals.append({"kind": "query_quality", "subject": "dead_query_rate",
                        "metric": round(q_dead / q_count, 3),
                        "detail": {"queries": q_count, "dead": q_dead}})
    # Grup B flip-karari sinyali: v4 vs golge (B6+B7). ORTALAMAYA degil KUYRUGA bak
    # (Fable): tail = |v4 - shadow| > 10 vaka. metric = ort kayma; detail.tail_rate
    # kritik. Faz-2 gecis karari yeterli tail vakasi + kabul edilebilir kayma ile.
    if shadow_deltas:
        _n = len(shadow_deltas)
        signals.append({"kind": "shadow_compare", "subject": "v4_vs_shadow",
                        "metric": round(sum(shadow_deltas) / _n, 1),
                        "detail": {"n": _n, "tail_gt10": shadow_tail,
                                   "tail_rate": round(shadow_tail / _n, 3),
                                   "max_abs": round(max(abs(d) for d in shadow_deltas), 1)}})

    # ---- Yaz (bugun icin idempotent: once bugunku sil) ----
    written = 0
    try:
        async with httpx.AsyncClient() as client:
            today = datetime.now(timezone.utc).date().isoformat()
            # F3: delete basarisizsa insert ETME (yoksa gunun sinyalleri duplike
            # olur). delete OK degilse bu dongu yazmayi atlar; bir sonraki dongu
            # (ya da manuel /run) temiz yeniden yazar.
            dr = await client.delete(
                f"{SUPABASE_URL}/rest/v1/improvement_signals?cycle_date=eq.{today}",
                headers=_headers(), timeout=15)
            if dr.status_code not in (200, 204):
                logger.warning(f"signal delete failed ({dr.status_code}) — insert atlandi")
                return {"ok": False, "error": "delete_failed"}
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
                    "avg_stability": avg_stab, "answers_no_source": no_source,
                    "adjacent_rate": round(q_adjacent / q_count, 3) if q_count else 0,
                    "dead_query_rate": round(q_dead / q_count, 3) if q_count else 0},
        "model_recognition": {e: round(m_recog[e] / m_total[e], 3) if m_total[e] else 0 for e in m_total},
        "signals_written": written,
    }
    logger.info(f"improvement cycle: {scanned} audits, {written} signals")

    # Faz0-0.1/0.3 — OTONOM BİLGİLENDİRME (kullanici: "hersey otonom, bilgilendirme yolla").
    # Amac arama motoru KALITESINI yukseltmek -> kurucu kalite sinyallerini/anomalileri
    # elle panele bakmadan gorur. Haftalik (notify=True) gonderilir; hata gonderimi kirmasin.
    if notify:
        try:
            lines = _quality_digest_lines(digest, m_total, m_recog, m_hallu, shadow_deltas, shadow_tail)
            if lines:
                from mailer import send_ticket_email
                subject = f"GEONI arama-kalite özeti · {datetime.now(timezone.utc).date().isoformat()}"
                await send_ticket_email(
                    FOUNDER_EMAIL, subject, "Arama motoru kalite raporu (otonom)", lines,
                    cta_label="Admin · İzleme", cta_url="https://app.geoni.ai/admin")
                logger.info("quality digest e-postasi gonderildi")
        except Exception as e:
            logger.warning(f"quality notify error: {e}")

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
