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
from result_contract import SHADOW_ENGINES  # golge motorlar (grok) canli-motor sinyallerine girmez

logger = logging.getLogger("self_improve")
_GEONI = re.compile(r"geoni", re.I)


SELF_SCAN_DOMAIN = "geoni.ai"
# Otonom bilgilendirme: haftalik kalite ozeti buraya gider (content_gen ile ayni kutu).
FOUNDER_EMAIL = os.environ.get("GEONI_CONTENT_EMAIL", "mail@geoni.ai")

# Faz1-1.3: DENEY DEFTERI — app_config'te (key=experiment:<isim>, value=JSON) hafif kayit.
# Yeni tablo YOK; _claim_daily_job ile ayni KV deposu. Otonom deneylerin (score_shadow,
# ileride motor-guven kalibrasyonu) hipotez/metrik/karar izini tutar (audit trail + guven).
import json as _json
_EXP_PREFIX = "experiment:"
# geoni.ai self-scan'in 4-motor sonucunun KALICI anlik goruntusu. audits.result_json
# retention'a tabi (bkz. _snapshot_self_recognition); app_config degil.
SELF_RECOG_KEY = "self_scan_recognition"


# Faz1-1.1: ACTION_POLICY — her sinyal icin AKSIYON ve otonom/onayli SINIRI (ÖLÇ->AKSIYON
# halkasini resmen kapatir; Fable vizyonu). "otonom" = motor kendi yapar (olcum/shadow/bildirim);
# "onayli" = musteri-gorunur (WEIGHTS/sorgu-havuzu/urun) -> asla otomatik, kurucu onayi (Ilke 16).
ACTION_POLICY = {
    "content_gap":     {"action": "content_gen haftalik icerik taslagi", "mode": "otonom"},
    "quality_model":   {"action": "engine_reliability hesapla -> score_shadow_v2 challenger besle", "mode": "otonom-shadow"},
    "shadow_compare":  {"action": "v4_to_shadow deneyi + esikte gecis-hazir mail", "mode": "yari-otonom"},
    "reweight_compare":{"action": "engine-reweight challenger deneyi (shadow)", "mode": "otonom-shadow"},
    "niche_pain":      {"action": "haftalik kalite mailinde acili nis; sik-tarama/urun", "mode": "otonom(bildirim)/onayli(aksiyon)"},
    "content_decay":   {"action": "bayatlayan hedef uyarisi (mail)", "mode": "otonom"},
    "query_quality":   {"action": "birincil-cevapsiz izle; sorgu-havuzu degisimi", "mode": "otonom(olcum)/onayli(havuz)"},
    "own_recognition": {"action": "kendi gorunurluk trendi; ani dususte alarm", "mode": "otonom"},
    "competitor_sov":  {"action": "SOV-drift ic-sinyal; urun karari", "mode": "otonom(sinyal)/onayli(urun)"},
    "WEIGHTS_change":  {"action": "skor formulu/agirlik", "mode": "ONAYLI (Ilke 16 musteri-skoru)"},
}


def _engine_reliability(m_total, m_recog, m_acc_sum, m_acc_n, m_hallu, m_contra) -> dict:
    """Faz1-1.4: quality_model'den motor GUVENILIRLIK carpani (0.1-1.0). Yuksek dogruluk +
    dusuk halusinasyon/celiski -> ~1.0; guvenilmez motor -> ~0.1. score_shadow_v2 challenger'i
    bu carpanla motorlari yeniden-agirliklar (SHADOW-only; manseti degistirmez)."""
    rel = {}
    for e in m_total:
        tot = m_total[e] or 1
        acc = (m_acc_sum[e] / m_acc_n[e] / 100.0) if m_acc_n.get(e) else 0.6
        hallu = m_hallu.get(e, 0) / tot
        contra = m_contra.get(e, 0) / tot
        r = max(0.1, min(1.0, acc * (1 - hallu) * (1 - contra)))
        rel[e] = round(r, 3)
    return rel


async def _load_experiment(client: httpx.AsyncClient, name: str) -> dict:
    try:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/app_config?key=eq.{_EXP_PREFIX}{name}&select=value",
            headers=_headers(), timeout=10)
        if r.status_code == 200 and r.json():
            return _json.loads(r.json()[0].get("value") or "{}")
    except Exception as e:
        logger.warning(f"_load_experiment({name}) error: {e}")
    return {}


async def _save_experiment(client: httpx.AsyncClient, name: str, data: dict) -> bool:
    try:
        r = await client.post(
            f"{SUPABASE_URL}/rest/v1/app_config?on_conflict=key",
            headers={**_headers(), "Prefer": "resolution=merge-duplicates"},
            json={"key": f"{_EXP_PREFIX}{name}", "value": _json.dumps(data),
                  "updated_at": datetime.now(timezone.utc).isoformat()},
            timeout=10)
        return r.status_code in (200, 201, 204)
    except Exception as e:
        logger.warning(f"_save_experiment({name}) error: {e}")
        return False


def _quality_digest_lines(digest: dict, m_total, m_recog, m_hallu,
                          shadow_deltas, shadow_tail, engine_rel=None) -> list:
    """Kurucuya gidecek KALITE ozeti (amac: arama motoru kalitesini yukseltmek —
    kalite sinyalleri gozle gorunur olsun). run_improvement_cycle'in locallerinden
    beslenir; salt-okuma, hicbir sey degistirmez."""
    q = digest.get("quality", {}) or {}
    ar = q.get("answer_rate") or 0
    lines = [
        f"Taranan tarama (7g): {digest.get('scanned_audits', 0)}",
        f"Sorgu cevap oranı: %{round(ar * 100)} · skor kararlılık ±{q.get('avg_stability')} · "
        f"kaynaksız cevap: {q.get('answers_no_source')}",
        f"Sorgu kalitesi: ölü sorgu %{round((q.get('dead_query_rate') or 0) * 100)} · "
        f"birincil-cevapsız %{round((q.get('primary_dead_rate') or 0) * 100)}",
    ]
    if m_total:
        lines.append("Motor tanınma: " + ", ".join(
            f"{e} %{round((m_recog.get(e, 0) / m_total[e]) * 100) if m_total[e] else 0}"
            for e in sorted(m_total)))
        worst = max(((e, (m_hallu.get(e, 0) / m_total[e]) if m_total[e] else 0) for e in m_total),
                    key=lambda x: x[1])
        lines.append(f"En yüksek halüsinasyon: {worst[0]} %{round(worst[1] * 100)}")
    # Fable 2026-07-23: reweight_compare (motor guvenilirligi) ARTIK maile giriyor —
    # eskiden sadece DB'ye yazilip kimseye ulasmiyordu (gemini reliability 0.27 gorunmezdi).
    if engine_rel:
        lines.append("Motor güvenilirliği (reweight): " + ", ".join(
            f"{e} {v}" for e, v in sorted(engine_rel.items(), key=lambda x: x[1])))
        we, wv = min(engine_rel.items(), key=lambda x: x[1])
        if wv < 0.4:
            lines.append(f"⚠️ En güvenilmez motor: {we} ({wv}) — ağırlığı gözden geçirilmeli (reweight_compare).")
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
    decay = digest.get("decay_domains") or []
    if decay:
        lines.append("⚠️ İçeriği bayatlayan hedefler (freshness düştü): " + ", ".join(decay))
    return lines


async def _snapshot_self_recognition() -> bool:
    """geoni.ai self-scan'in 4-motor `model_results`'ini app_config'e KOPYALAR.

    NEDEN (2026-08-01): own_recognition sinyali audits.result_json'dan okunuyordu,
    ama o alan kalici DEGIL — apply_audit_retention RPC'si ayni (kullanici+tur+hedef)
    icin en yeni kayit disindaki HER raporun result_json'unu YASINA BAKMADAN
    NULL'liyor (rn > 1). geoni.ai elle bir kez daha taraninca haftalik self-scan
    kaydi bosaliyor, sinyal sessizce oluyordu. app_config retention'a tabi olmadigi
    icin anlik goruntu kalici; kaynak zinciri run_improvement_cycle'da (1)/(2)/(3).
    Salt-kopya: hicbir tarama/skor davranisi degismez."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/audits?type=eq.web&domain=eq.{SELF_SCAN_DOMAIN}"
                f"&status=eq.complete&result_json->>auto_monitor=eq.true"
                f"&select=result_json,created_at&order=created_at.desc&limit=1",
                headers=_headers(), timeout=15)
            rows = r.json() if r.status_code == 200 else []
            if not rows:
                logger.warning("self_scan snapshot: taze self-scan kaydi bulunamadi")
                return False
            rj = rows[0].get("result_json") or {}
            mr = rj.get("model_results") or (rj.get("brand_recall") or {}).get("model_results") or {}
            if not mr:
                logger.warning("self_scan snapshot: model_results bos, yazilmadi")
                return False
            return await _save_experiment(client, SELF_RECOG_KEY, {
                "at": rows[0].get("created_at"),
                "score": rj.get("score"),
                "model_results": mr,
            })
    except Exception as e:
        logger.warning(f"_snapshot_self_recognition error: {e}")
        return False


async def self_scan() -> int | None:
    """geoni.ai'yi tarar (kendi AI-gorunurluk skor trendimiz icin) ve audits'e
    yazar. monitor._scan_web_item'i tekrar kullanir; haftada bir yeter."""
    try:
        from monitor import _scan_web_item
        score = await _scan_web_item({"target": {"domain": SELF_SCAN_DOMAIN}})
        logger.info(f"self-scan {SELF_SCAN_DOMAIN}: score={score}")
        # Retention kaydi bosaltmadan ONCE kalici kopya al (own_recognition kaynagi).
        await _snapshot_self_recognition()
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


async def _content_decay_signals(days: int = 30) -> list:
    """Faz2-2.3: content_decay — WEB hedeflerinin freshness'i son taramada DUSUYOR mu
    (icerik bayatliyor). Ayri fetch (web audits, sayfali); salt-olcum, otonom-guvenli.
    domain basina en yeni iki taramayi kiyaslar; anlamli dusus (>=5 puan) -> sinyal."""
    out = []
    if not SUPABASE_URL:
        return out
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    rows = []
    try:
        async with httpx.AsyncClient() as client:
            offset = 0
            while True:
                r = await client.get(
                    f"{SUPABASE_URL}/rest/v1/audits?select=domain,result_json,created_at"
                    f"&type=eq.web&status=eq.complete&created_at=gte.{since}"
                    f"&order=created_at.desc&limit=1000&offset={offset}",
                    headers=_headers(), timeout=40)
                batch = r.json() if r.status_code == 200 else []
                rows.extend(batch)
                if len(batch) < 1000 or offset >= 20000:
                    break
                offset += 1000
    except Exception as e:
        logger.warning(f"content_decay fetch error: {e}")
        return out
    by_dom = defaultdict(list)  # domain -> [freshness] (created_at.desc sirali)
    for a in rows:
        rj = a.get("result_json") or {}
        if not isinstance(rj, dict):
            continue
        fr = (rj.get("score_breakdown") or {}).get("freshness")
        dom = a.get("domain")
        if dom and isinstance(fr, (int, float)):
            by_dom[dom].append(float(fr))
    for dom, fresh in by_dom.items():
        if len(fresh) < 2:
            continue
        latest, prev = fresh[0], fresh[1]  # desc: [0] en yeni
        delta = round(latest - prev, 1)
        if delta <= -5:  # anlamli freshness dususu -> icerik bayatliyor
            out.append({"kind": "content_decay", "subject": dom[:200], "metric": delta,
                        "detail": {"latest": latest, "previous": prev, "scans": len(fresh)}})
    return out


async def run_improvement_cycle(days: int = 7, top_n: int = 25, notify: bool = False) -> dict:
    """Son `days` gunun brand/person/social taramalarini hasat eder, A/B/C/Q
    sinyallerini hesaplar, improvement_signals'e (bugun icin) yazar ve digest doner."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"ok": False, "error": "not_configured"}
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    geoni_mr = {}
    geoni_mr_at = None      # anlik goruntunun tarihi (own_recognition detail'ine girer)
    geoni_mr_src = None     # hangi kaynaktan okundu (snapshot / audit_top / audit_nested)
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
            # geoni.ai self-scan (own_recognition) — UC KADEMELI kaynak.
            # (1) app_config anlik goruntusu: BIRINCIL kaynak. NEDEN: audits.result_json
            #     kalici DEGIL — apply_audit_retention RPC'si ayni (kullanici+tur+hedef)
            #     icin en yeni kayit disindaki HER raporun result_json'unu YASINA BAKMADAN
            #     NULL'liyor. geoni.ai elle bir kez daha taraninca haftalik self-scan
            #     kaydi bosaliyor ve sinyal SESSIZCE oluyordu (2026-08-01'de oldu:
            #     own_recognition 9 gun uretildikten sonra bir anda kayboldu).
            #     _snapshot_self_recognition() bu yuzden tarama aninda kopya birakir.
            snap = await _load_experiment(client, SELF_RECOG_KEY)
            if isinstance(snap, dict) and isinstance(snap.get("model_results"), dict):
                geoni_mr = snap["model_results"] or {}
                geoni_mr_at, geoni_mr_src = snap.get("at"), "snapshot"
            # (2) Yedek: haftalik self_scan kaydi (auto_monitor=true) — top-level yazar.
            if not geoni_mr:
                gm = await client.get(
                    f"{SUPABASE_URL}/rest/v1/audits?type=eq.web&domain=eq.geoni.ai"
                    f"&status=eq.complete&result_json->>auto_monitor=eq.true"
                    f"&select=result_json,created_at&order=created_at.desc&limit=1",
                    headers=_headers(), timeout=15,
                )
                g = gm.json() if gm.status_code == 200 else []
                if g:
                    geoni_mr = (g[0].get("result_json") or {}).get("model_results") or {}
                    if geoni_mr:
                        geoni_mr_at, geoni_mr_src = g[0].get("created_at"), "audit_top"
            # (3) Son care: normal (kullanici) geoni.ai web taramasi — main.py bu yolda
            #     model_results'i brand_recall ALTINA yazar. Ic-ice okumak "sessiz bos"
            #     durumundan iyidir; kaynak detail'de acikca isaretlenir.
            if not geoni_mr:
                gm2 = await client.get(
                    f"{SUPABASE_URL}/rest/v1/audits?type=eq.web&domain=eq.geoni.ai"
                    f"&status=eq.complete&result_json=not.is.null"
                    f"&select=result_json,created_at&order=created_at.desc&limit=1",
                    headers=_headers(), timeout=15,
                )
                g2 = gm2.json() if gm2.status_code == 200 else []
                if g2:
                    rj = g2[0].get("result_json") or {}
                    geoni_mr = ((rj.get("brand_recall") or {}).get("model_results")
                                or rj.get("model_results") or {})
                    if geoni_mr:
                        geoni_mr_at, geoni_mr_src = g2[0].get("created_at"), "audit_nested"
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
    q_primary = 0                             # birincil (adjacent OLMAYAN) sorgu sayisi
    q_dead = 0                                # HIC motor cevaplamadi — zayif sorgu
    q_primary_dead = 0                        # birincil sorgu ama hic motor cevaplamadi
    # NOT: adjacent sorgular KASITLI (SOV_ADJACENT_COUNT komsu-alan; 2/5=%40 tasarim).
    # O yuzden "adjacent_rate" bir sabittir, kalite metrigi DEGIL — olcmuyoruz. Gercek
    # kalite = birincil sorgularin cevapsiz kalma orani (query uretimi cekirdek konuda zayif mi).
    m_total = defaultdict(int)                # 4-motor (model_results: claude/gemini/openai/perplexity)
    m_recog = defaultdict(int)                # taninma
    m_acc_sum = defaultdict(float)            # dogruluk_skoru toplami
    m_acc_n = defaultdict(int)
    m_hallu = defaultdict(int)                # uydurma_suphesi
    m_contra = defaultdict(int)               # celiski_var
    shadow_deltas = []                        # Grup B: v4 score - score_shadow (flip karari)
    shadow_tail = 0                           # |delta| > 10 (KUYRUK — B7'nin etkiledigi uc vakalar)
    audit_engine_scores = []                  # Faz1-1.4: audit basina {motor: per-engine score}
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
            _is_adj = bool(q.get("adjacent"))   # KASITLI komsu-alan sorgusu (design)
            if not _is_adj:
                q_primary += 1
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
                if not _is_adj:     # birincil sorgu cevapsiz -> gercek uretim kalite sinyali
                    q_primary_dead += 1
        # 4-MOTOR kalite (model_results): taninma + dogruluk + halusinasyon + celiski.
        # Golge motorlar (grok) HARIC: self_improve CANLI skorlamayi iyilestirir; agirlik-0
        # grok canli motor degil, sinyalleri (quality_model/own_recognition) kirletmemeli.
        # Grok golge-degerlendirmesi ayri (audits.model_results.grok + haftalik hook).
        for meng, mv in (rj.get("model_results") or {}).items():
            if not isinstance(mv, dict) or meng in SHADOW_ENGINES:
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
        # Faz1-1.4: bu audit'in per-engine skorlari (reweight backtest icin) — golge haric
        esc = {k: mv.get("score") for k, mv in (rj.get("model_results") or {}).items()
               if isinstance(mv, dict) and k not in SHADOW_ENGINES and isinstance(mv.get("score"), (int, float))}
        if esc:
            audit_engine_scores.append(esc)

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
        if isinstance(mv, dict) and meng not in SHADOW_ENGINES:  # golge motor (grok) own_recognition'a girmez
            signals.append({"kind": "own_recognition", "subject": meng,
                            "metric": 1 if mv.get("recognized") else 0,
                            # as_of/source: sinyal BAYAT mi (self_scan haftalik) ve hangi
                            # kaynaktan geldi — sessizce sabit kalan trend fark edilsin.
                            "detail": {"score": mv.get("score"),
                                       "as_of": geoni_mr_at, "source": geoni_mr_src}})

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
        signals.append({"kind": "query_quality", "subject": "dead_query_rate",
                        "metric": round(q_dead / q_count, 3),
                        "detail": {"queries": q_count, "dead": q_dead}})
    if q_primary:
        signals.append({"kind": "query_quality", "subject": "primary_dead_rate",
                        "metric": round(q_primary_dead / q_primary, 3),
                        "detail": {"primary_queries": q_primary, "dead": q_primary_dead,
                                   "note": "birincil sorgu hic cevap almadi (uretim zayif)"}})
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

    # Faz1-1.4: engine-reweight CHALLENGER (offline backtest, SHADOW-only, musteri-skoru
    # DEGISMEZ). Motor-portresini champion (WEIGHTS) vs challenger (WEIGHTS×guvenilirlik)
    # yeniden-agirlikla kiyaslar; fark = guvenilmez motorun (or. gemini %33 halusinasyon)
    # skordaki etkisinin ne kadar azalacagi. gemini golge-cikisinin OTOMATIK, surekli hali.
    # quality_model sinyali -> bu challenger'i besler (ACTION_POLICY). Yeterli veri + kararli
    # fark olunca kademeli terfi (onayli, Ilke 16). Hicbir seyi otomatik degistirmez.
    engine_rel = _engine_reliability(m_total, m_recog, m_acc_sum, m_acc_n, m_hallu, m_contra)
    if audit_engine_scores and engine_rel:
        from brand_recall import WEIGHTS as _BW
        ew = {k: _BW.get(k, 0) for k in ("claude", "openai", "gemini", "perplexity")}
        rw_deltas = []
        for esc in audit_engine_scores:
            present = [k for k in esc if ew.get(k, 0) > 0]
            cw = sum(ew[k] for k in present)
            chw = sum(ew[k] * engine_rel.get(k, 1.0) for k in present)
            if cw > 0 and chw > 0:
                champ = sum(esc[k] * ew[k] for k in present) / cw
                chall = sum(esc[k] * ew[k] * engine_rel.get(k, 1.0) for k in present) / chw
                rw_deltas.append(chall - champ)
        if rw_deltas:
            _n = len(rw_deltas)
            _tail = sum(1 for d in rw_deltas if abs(d) > 10)
            signals.append({"kind": "reweight_compare", "subject": "model_portion",
                            "metric": round(sum(rw_deltas) / _n, 1),
                            "detail": {"n": _n, "tail_gt10": _tail,
                                       "tail_rate": round(_tail / _n, 3),
                                       "reliability": engine_rel}})

    # Faz2-2.3: content_decay (izlenen web hedeflerinde freshness dususu) — ayri fetch.
    signals.extend(await _content_decay_signals())

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

    # Faz1-1.1: KALITE REGRESYON ALARMI (otonom, anlik). Cevap orani bir onceki
    # donguye gore ANI dususse (arama motoru kalitesi bozulmasi) kurucuya HEMEN uyar
    # (haftalik ozeti bekleme). Salt-bildirim; hicbir sey degistirmez.
    cur_ar = round(q_answered / q_total, 3) if q_total else None
    if cur_ar is not None:
        try:
            async with httpx.AsyncClient() as _rc:
                today_s = datetime.now(timezone.utc).date().isoformat()
                pr = await _rc.get(
                    f"{SUPABASE_URL}/rest/v1/improvement_signals?kind=eq.quality_overall"
                    f"&subject=eq.answer_rate&cycle_date=lt.{today_s}"
                    f"&select=metric,cycle_date&order=cycle_date.desc&limit=1",
                    headers=_headers(), timeout=10)
                prev = pr.json() if pr.status_code == 200 else []
                if prev and isinstance(prev[0].get("metric"), (int, float)):
                    prev_ar = float(prev[0]["metric"])
                    if prev_ar - cur_ar >= 0.10:  # >=10 puan dusus -> regresyon
                        from mailer import send_ticket_email
                        await send_ticket_email(
                            FOUNDER_EMAIL, "⚠️ GEONI: arama kalitesi düştü",
                            "Sorgu cevap oranında ani düşüş (otonom alarm)",
                            [f"Önceki döngü ({prev[0].get('cycle_date')}): %{round(prev_ar * 100)}",
                             f"Bugün: %{round(cur_ar * 100)}",
                             f"Düşüş: {round((prev_ar - cur_ar) * 100)} puan — motor/sağlayıcı/skorlama incelenmeli."],
                            cta_label="Admin · İzleme", cta_url="https://app.geoni.ai/admin")
                        logger.warning(f"KALITE REGRESYON: answer_rate {prev_ar}->{cur_ar}, kurucuya bildirildi")
        except Exception as e:
            logger.warning(f"regression alert error: {e}")

    # Fable #4 (2026-07-23): own_recognition "ANI DUSUS alarmi" — ACTION_POLICY (satir 52)
    # bu davranisi vaadediyordu ama KOD YOKTU (olculuyor, DB'ye yaziliyor, kimseye gitmiyordu).
    # Bir AI motoru GEONI'yi onceki cycle TANIYORDU ama ARTIK tanimiyorsa (1->0) kurucuya HEMEN
    # uyar — kendi gorunurlugumuz en kritik buyume sinyali. Salt-bildirim.
    cur_own = {e: (1 if (mv or {}).get("recognized") else 0)
               for e, mv in (geoni_mr or {}).items()
               if isinstance(mv, dict) and e not in SHADOW_ENGINES}
    if cur_own:
        try:
            async with httpx.AsyncClient() as _rc:
                today_s = datetime.now(timezone.utc).date().isoformat()
                pr = await _rc.get(
                    f"{SUPABASE_URL}/rest/v1/improvement_signals?kind=eq.own_recognition"
                    f"&cycle_date=lt.{today_s}&select=subject,metric,cycle_date,detail"
                    f"&order=cycle_date.desc&limit=20",
                    headers=_headers(), timeout=10)
                rows = pr.json() if pr.status_code == 200 else []
                prev_own, prev_src = {}, None
                if rows:
                    latest = rows[0]["cycle_date"]
                    prev_src = ((rows[0].get("detail") or {}) if isinstance(rows[0].get("detail"), dict) else {}).get("source")
                    for r in rows:
                        if r.get("cycle_date") == latest and r.get("subject") not in prev_own:
                            prev_own[r["subject"]] = float(r.get("metric") or 0)
                drops = [e for e, v in cur_own.items() if v == 0 and prev_own.get(e, 0) >= 1]
                # ELMA-ARMUT KORUMASI: iki olcum FARKLI kaynaktan geliyorsa dusus
                # gercek olmayabilir — haftalik self-scan (tr) ile araya giren elle
                # yapilmis bir tarama (or. en) kiyaslanirsa "motor bizi tanimayi
                # birakti" YANLIS alarmi gider. Ayni kaynak degilse yalniz logla.
                if drops and prev_src != geoni_mr_src:
                    logger.warning(
                        f"own_recognition dusus ({drops}) ATLANDI: kaynak degisti "
                        f"{prev_src} -> {geoni_mr_src}; kiyas gecerli degil")
                    drops = []
                if drops:
                    from mailer import send_ticket_email
                    still = [e for e, v in cur_own.items() if v >= 1]
                    await send_ticket_email(
                        FOUNDER_EMAIL, "⚠️ GEONI: bir AI motoru bizi tanımayı bıraktı",
                        "Kendi görünürlük düşüşü (own_recognition, otonom alarm)",
                        [f"Artık GEONI'yi tanımayan motor(lar): {', '.join(drops)}",
                         f"Hâlâ tanıyanlar: {', '.join(still) or 'yok'}",
                         "AI görünürlüğümüz düştü — schema/otorite/lansman kanıtı gözden geçirilmeli."],
                        cta_label="Admin · İzleme", cta_url="https://app.geoni.ai/admin")
                    logger.warning(f"OWN_RECOGNITION DUSUS: {drops} artik tanimiyor, kurucuya bildirildi")
        except Exception as e:
            logger.warning(f"own_recognition alert error: {e}")

    # Fable #4: own_visibility "ILK DIS ATIF" milestone — GEONI ilk kez bir AI cevabinda
    # kaynak/atif olarak gecince (7 gundur 0'di) kurucuya TEK SEFER kutlama. En kritik buyume sinyali.
    if sum(own_mention.values()) > 0:
        try:
            async with httpx.AsyncClient() as _rc:
                pr = await _rc.get(
                    f"{SUPABASE_URL}/rest/v1/improvement_signals?kind=eq.own_visibility"
                    f"&metric=gt.0&cycle_date=lt.{datetime.now(timezone.utc).date().isoformat()}"
                    f"&select=cycle_date&limit=1",
                    headers=_headers(), timeout=10)
                had_before = (not (pr.status_code == 200 and not pr.json()))  # emin degilsek gonderME
                if not had_before:
                    from mailer import send_ticket_email
                    top = [f"{e}: {c}" for e, c in sorted(own_mention.items(), key=lambda x: -x[1]) if c > 0]
                    await send_ticket_email(
                        FOUNDER_EMAIL, "🎉 GEONI: ilk kez bir AI bizi kaynak gösterdi!",
                        "own_visibility ilk dış atıf (milestone)",
                        ["GEONI artık AI cevaplarında geçiyor — motor bazlı: " + ", ".join(top),
                         "En kritik büyüme sinyali. İlk atıf yakalandı."],
                        cta_label="Admin · İzleme", cta_url="https://app.geoni.ai/admin")
                    logger.warning(f"OWN_VISIBILITY ILK ATIF: {dict(own_mention)}, kurucuya bildirildi")
        except Exception as e:
            logger.warning(f"own_visibility milestone error: {e}")

    # Faz1-1.3 + Karar#1: v4->golge skor gecisini FORMAL deney olarak izle. Esik
    # karsilaninca kurucuya TEK SEFER "hazir, onaylıyor musun" e-postasi (yari-otonom:
    # tetik otonom, UYGULAMA hep onayli — skor musteri-gorunur ucretli sozlesme, asla
    # otomatik degismez). score_shadow verisi birikene kadar sessizce "collecting" kalir.
    if shadow_deltas:
        try:
            _n = len(shadow_deltas)
            _tail = shadow_tail / _n
            async with httpx.AsyncClient() as _c:
                exp = await _load_experiment(_c, "v4_to_shadow")
                was_ready = exp.get("status") == "ready"
                exp.update({
                    "hypothesis": "golge skor (B6+B7) v4 mansetini guvenle degistirebilir",
                    "last_cycle": datetime.now(timezone.utc).date().isoformat(),
                    "n": _n, "tail_rate": round(_tail, 3),
                    "mean_delta": round(sum(shadow_deltas) / _n, 1),
                    "criteria": "n>=50 ve tail_rate<0.10",
                })
                is_ready = _n >= 50 and _tail < 0.10
                exp["status"] = "ready" if is_ready else "collecting"
                await _save_experiment(_c, "v4_to_shadow", exp)
                if is_ready and not was_ready:  # yeni HAZIR oldu -> bir kez bildir
                    from mailer import send_ticket_email
                    await send_ticket_email(
                        FOUNDER_EMAIL, "GEONI: v4→gölge skor geçişi HAZIR",
                        "Skorlama faz-2 geçiş kriterleri karşılandı (onay bekliyor)",
                        [f"Örneklem: {_n} tarama", f"Kuyruk sapma: %{round(_tail * 100)} (<%10 hedef)",
                         f"Ortalama kayma: {round(sum(shadow_deltas) / _n, 1)}",
                         "Gölge skor v4 manşetini güvenle değiştirebilir görünüyor. Uygulamamı onaylıyor musun?"],
                        cta_label="Admin · İzleme", cta_url="https://app.geoni.ai/admin")
                    logger.info("v4_to_shadow experiment READY — kurucuya bildirildi")
        except Exception as e:
            logger.warning(f"experiment ledger error: {e}")

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
                    "dead_query_rate": round(q_dead / q_count, 3) if q_count else 0,
                    "primary_dead_rate": round(q_primary_dead / q_primary, 3) if q_primary else 0},
        "decay_domains": [s["subject"] for s in signals if s.get("kind") == "content_decay"][:5],
        "model_recognition": {e: round(m_recog[e] / m_total[e], 3) if m_total[e] else 0 for e in m_total},
        "signals_written": written,
    }
    logger.info(f"improvement cycle: {scanned} audits, {written} signals")

    # Faz0-0.1/0.3 — OTONOM BİLGİLENDİRME (kullanici: "hersey otonom, bilgilendirme yolla").
    # Amac arama motoru KALITESINI yukseltmek -> kurucu kalite sinyallerini/anomalileri
    # elle panele bakmadan gorur. Haftalik (notify=True) gonderilir; hata gonderimi kirmasin.
    if notify:
        try:
            lines = _quality_digest_lines(digest, m_total, m_recog, m_hallu, shadow_deltas, shadow_tail, engine_rel)
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
