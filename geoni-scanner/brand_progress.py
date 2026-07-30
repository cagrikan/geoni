"""Marka/kisi/sosyal taramasinin ILERLEME fazlari — saf mantik.

Neden ayri modul: bu mantik main.py'de duruyordu ve testi main'i (dolayisiyla
fastapi/pydantic'i) import etmek zorunda birakiyordu. Deploy kapisi
(.github/workflows) testleri BILEREK minimal ortamda kosuyor — yalniz
pytest/httpx/cbor2/cryptography — cunku testler hizli ve bagimsiz kalsin
isteniyor (bkz. result_contract.py basligi). Saf mantik burada durursa hem CI'da
kosar hem main.py ince kalir.

Kaynak sozlesme: brand_recall'un `on_step` anahtarlari (PROGRESS_MESSAGES).
"""

# Istemciye bildirilen FAZLAR (sirali) — ilerleme yuzdesi bu siradan uretilir.
# `sov` KENDI fazi degildir: SOV arka planda create_task ile baslatilip aninda
# querying_models'e gecilir, ayri faz gosterilse ekranda milisaniye gorunurdu.
# `model_answered`/`model_no_answer` faz degil, faz-ici sayactir.
BRAND_PROGRESS_STEPS = ["web_search", "verifying_identity", "querying_models",
                        "comparing", "scoring"]
BRAND_STEP_ALIAS = {"sov": "querying_models"}

# Paralel sorgulanan motor sayisi (claude/openai/gemini/perplexity + golge grok).
BRAND_MODEL_COUNT = 5


def yeni_brand_ilerlemesi() -> dict:
    return {"step": None, "index": 0, "total": len(BRAND_PROGRESS_STEPS),
            "models_done": 0, "models_total": BRAND_MODEL_COUNT}


def brand_ilerleme_guncelle(p: dict, anahtar: str) -> dict:
    """brand_recall'un adim anahtarini ilerleme sozlugune isler (yerinde).

    MONOTONIK: verifying_identity kosula bagli oldugu ve `sov` faz degistirmedigi
    icin indeks ASLA geri gitmez — ilerleme cubugunun geri kaymasi kullanicida
    "tarama bastan basladi" izlenimi yaratirdi.
    """
    if anahtar in ("model_answered", "model_no_answer"):
        p["models_done"] = min(p["models_total"], p["models_done"] + 1)
        return p
    faz = BRAND_STEP_ALIAS.get(anahtar, anahtar)
    if faz not in BRAND_PROGRESS_STEPS:
        return p
    i = BRAND_PROGRESS_STEPS.index(faz)
    if p["step"] is None or i > p["index"]:
        p["index"] = i
        p["step"] = faz
    return p
