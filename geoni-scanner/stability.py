"""
GEONI Scanner - Skor Istikrari (oynaklik yumusatma + degisim kaynagi)

Web-tabanli motorlarin yanitlari taramadan taramaya dalgalanir (ornek:
Perplexity tanima bacagi ayni gunde 96 <-> 30 oynadi). Kullanici hicbir
sey degistirmemisken skorun ±5-10 puan oynamasi "ne yaptim?" panigi ya da
"olcum guvenilmez" suphesi yaratiyor. Bu modul iki sey uretir:

1. smoothed_score: son 3 taramanin agirlikli ortalamasi (0.5/0.3/0.2,
   eksik gecmiste yeniden normalize) — tek taramalik dalgalanma tabloyu
   domine etmez, gercek iyilesme yine gorunur.
2. driver: skor onceki taramaya gore belirgin degistiyse ve degisimin
   coğu TEK bir bilesenden geliyorsa o bilesen isaretlenir — rapor
   "bu degisim buyuk olcude X bacagindaki dalgalanmadan geliyor" der.

Sonuc result_payload["stability"] olarak tasinir; eski kayitlarla geriye
uyumlu (alan yoksa arayuz hicbir sey gostermez).
"""

import logging

logger = logging.getLogger(__name__)

SMOOTH_WEIGHTS = (0.5, 0.3, 0.2)   # simdiki, onceki, evvelki
DRIVER_MIN_DELTA = 5               # not gosterme esigi (puan)
DRIVER_MIN_SHARE = 0.5             # baskin bilesen esigi

# Kisi/marka taramasinda breakdown anahtari -> skor agirligi eslemesi
# (bkz. brand_recall.WEIGHTS / WEIGHTS_SOV). SOV'lu ve SOV'suz iki duzen.
_BRAND_WEIGHTS_SOV = {
    "claude": 0.12, "chatgpt": 0.18, "gemini": 0.18, "perplexity": 0.12,
    "yanit_kalitesi": 0.05, "konu_uyumu": 0.05, "kategori_gorunurlugu": 0.30,
}
_BRAND_WEIGHTS = {
    "claude": 0.16, "chatgpt": 0.24, "gemini": 0.24, "perplexity": 0.16,
    "yanit_kalitesi": 0.10, "konu_uyumu": 0.10,
}


def _brand_weights_for(breakdown: dict) -> dict:
    return _BRAND_WEIGHTS_SOV if "kategori_gorunurlugu" in (breakdown or {}) else _BRAND_WEIGHTS


def compute_stability(current_score, current_breakdown: dict,
                      prev_audits: list, weights: dict | None = None) -> dict | None:
    """
    prev_audits: [{"score": int, "breakdown": dict}, ...] — en yeni once,
    simdiki tarama HARIC. Bos ise None doner (ilk tarama: not yok).
    weights: web taramasinda weights_used; kisi/markada None birak
    (breakdown yapisina gore secilir).
    """
    prevs = [p for p in (prev_audits or []) if p.get("score") is not None]
    if current_score is None or not prevs:
        return None

    # 1) Yumusatilmis skor
    scores = [float(current_score)] + [float(p["score"]) for p in prevs[:2]]
    ws = SMOOTH_WEIGHTS[:len(scores)]
    smoothed = sum(s * w for s, w in zip(scores, ws)) / sum(ws)

    prev = prevs[0]
    delta = int(round(current_score - prev["score"]))

    out = {
        "prev_score": int(prev["score"]),
        "smoothed_score": int(round(smoothed)),
        "delta": delta,
        "driver": None,
        "driver_delta": None,
        "driver_share": None,
    }

    # 2) Degisim kaynagi: agirlikli katkilarin en buyugu
    prev_bd = prev.get("breakdown") or {}
    cur_bd = current_breakdown or {}
    if weights is None:
        weights = _brand_weights_for(cur_bd)
    contribs = {}
    for key, cur_val in cur_bd.items():
        try:
            prev_val = float(prev_bd.get(key))
            w = float(weights.get(key, 0))
        except (TypeError, ValueError):
            continue
        if w <= 0:
            continue
        contribs[key] = (float(cur_val) - prev_val) * w

    total = sum(abs(c) for c in contribs.values())
    if contribs and total > 0 and abs(delta) >= DRIVER_MIN_DELTA:
        driver = max(contribs, key=lambda k: abs(contribs[k]))
        share = abs(contribs[driver]) / total
        if share >= DRIVER_MIN_SHARE:
            out["driver"] = driver
            out["driver_delta"] = round(float(cur_bd[driver]) - float(prev_bd[driver]), 1)
            out["driver_share"] = round(share, 2)

    return out


async def build_stability(kind: str, target: str, current_score,
                          current_breakdown: dict, weights: dict | None = None) -> dict | None:
    """Onceki taramalari cekip compute_stability'yi calistirir (fail-silent)."""
    try:
        from db import get_previous_audits
        prevs = await get_previous_audits(kind, target, limit=2)
        return compute_stability(current_score, current_breakdown, prevs, weights)
    except Exception as e:
        logger.info(f"stability hesaplanamadi ({kind}/{target}): {e}")
        return None
