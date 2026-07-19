#!/usr/bin/env python3
"""A4-4 (QA 2026-07-19): gecelik canary — canli olcum bozulmasini SESSIZ birakmaz.

Sabit, taninmis bir hedefi (@garyvee) gercek pipeline'dan gecirir ve dogrular:
- payload sekli (result_contract.BRAND_CLIENT_KEYS hepsi var mi) -> payload-drop sinifi
- skor makul bantta + recognized True (taninmis hesap) -> olcum tamamen kirilmis mi
- sov.competitors bos mu (SOFT uyari; A4-2'nin canli nabzi)
Basarisizsa exit!=0 -> GitHub Actions job kirmizi olur (bildirim). Bu haftaki pahali
elle-QA'nin surekli + ucretsiz hali.

Kullanim: INTERNAL_SCAN_TOKEN=... python scripts/canary.py  [--api https://api.geoni.ai]
"""
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from result_contract import BRAND_CLIENT_KEYS  # noqa: E402

API = os.environ.get("CANARY_API", "https://api.geoni.ai")
TOKEN = os.environ.get("INTERNAL_SCAN_TOKEN", "")
HANDLE = os.environ.get("CANARY_HANDLE", "garyvee")
NICHE = os.environ.get("CANARY_NICHE", "entrepreneurship marketing")


def _post(path, body):
    req = urllib.request.Request(
        f"{API}{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Internal-Scan": TOKEN}, method="POST")
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def _get(path):
    req = urllib.request.Request(f"{API}{path}", headers={"X-Internal-Scan": TOKEN})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def main() -> int:
    fails, warns = [], []
    # force=true: cache'i atla, GERCEK pipeline'i olc.
    start = _post("/api/social-check",
                  {"handle": HANDLE, "niche": NICHE, "email": "canary@geoni.ai",
                   "lang": "en", "force": True})
    job = start.get("job_id")
    if not job:
        print(f"CANARY FAIL: job baslatilamadi: {start}")
        return 1

    result = None
    for _ in range(60):  # ~7.5 dk (cold-start dahil)
        st = _get(f"/api/brand-check/{job}")
        if st.get("status") == "complete":
            result = st.get("result") or {}
            break
        if st.get("status") == "failed":
            print(f"CANARY FAIL: tarama failed: {st}")
            return 1
        time.sleep(8)
    if result is None:
        print("CANARY FAIL: tarama zaman asimi (cold-start > 7.5dk ya da asili)")
        return 1

    # 1) Sozlesme: client'in okudugu her anahtar var mi (payload-drop koruma)
    missing = BRAND_CLIENT_KEYS - set(result.keys())
    if missing:
        fails.append(f"payload eksik anahtar: {missing}")

    # 2) Olcum makul mu
    score = result.get("score")
    if not isinstance(score, (int, float)) or not (0 <= score <= 100):
        fails.append(f"skor bant disi: {score}")
    if result.get("recognized") is not True:
        fails.append(f"taninmis @{HANDLE} recognized=False -> olcum kirilmis olabilir")
    if score is not None and score < 20:
        warns.append(f"taninmis hesap skoru dusuk ({score}) -> saglayici/olcum kontrol")

    # 3) Sosyal kimlik cozumu (bu turun KRITIK regresyonu)
    if not result.get("resolved_identity"):
        fails.append("resolved_identity BOS -> sosyal kimlik cozumu kirik (KRITIK regresyon sinifi)")

    # 4) SOV + competitors nabzi (A4-2)
    sov = result.get("sov") or {}
    if not sov.get("competitors"):
        warns.append("sov.competitors BOS -> A4-2 nabzi (LLM/kredi/filtre kontrol)")

    for w in warns:
        print(f"CANARY WARN: {w}")
    if fails:
        for f in fails:
            print(f"CANARY FAIL: {f}")
        return 1
    print(f"CANARY OK: @{HANDLE} score={score} recognized={result.get('recognized')} "
          f"identity={result.get('resolved_identity')} competitors={len(sov.get('competitors') or [])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
