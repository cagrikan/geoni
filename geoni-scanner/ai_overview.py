"""
GEONI Scanner - Google AI Overview (AIO) Takibi

NEDEN AYRI BIR MODUL: sov.py'nin "google" motoru Google Search grounding'li
Gemini'dir ve yorumunda acikca "AI Overviews'un ESDEGERI" der (sov.py:14-17,
brand_recall.py:620). Bu bir VEKIL olcumdur, AIO'nun kendisi degil:
  - AIO bir SERP ozelligidir; her sorguda cikmaz, kendi tetiklenme kurali vardir.
  - AIO'nun kaynak secimi Gemini grounding'inkinden farklidir.
Musteri raporu acip yanina Google'da ayni aramayi yaptiginda IKI FARKLI sonuc
goruyordu. Bu ozellik eksikliginden kotudur - guven kaybidir. Bu modul gercek
SERP'ten AIO kutusunu okur.

SKORA GIRMEZ (bilincli). Grok golge-mod deseni ([[geoni-grok-golge-mod]]):
once veri birikir, redundans olculur, ancak ondan sonra WEIGHTS'e pay verilir.
Skor/sema degisikligi ayri bir karardir.

SAGLAYICI: DataForSEO SERP API (live/advanced). Olculen fiyat 2026-08-02:
  temel canli SERP $0.002 + load_async_ai_overview icin ekstra $0.002.
  Ekstra ucret, AIO yoksa ya da asenkron degilse HESABA IADE EDILIR (resmi
  doküman). 5 sorgulu bir taramada en kotu durum ~$0.02.
Kimlik yoksa modul sessizce devre disi kalir (None doner) - tarama bozulmaz.

Marka/domain eslesmesi icin sov.py'nin TEST EDILMIS yardimcilari kullanilir;
ikinci bir eslestirici yazmak iki mantigin zamanla ayrisma riskini dogurur.
"""

import asyncio
import base64
import logging
import os
from collections import Counter

import httpx

import sov

logger = logging.getLogger(__name__)

DFS_LOGIN = os.environ.get("DATAFORSEO_LOGIN", "")
DFS_PASSWORD = os.environ.get("DATAFORSEO_PASSWORD", "")
DFS_HOST = os.environ.get("DATAFORSEO_HOST", "api.dataforseo.com")

# Konum/dil kodlari canli API'den dogrulandi (2026-08-02, /v3/serp/google/locations):
# Turkiye=2792 (iso TR), United States=2840 (iso US); dil kodlari "tr" ve "en" mevcut.
_LOCATION = {"tr": 2792, "en": 2840}

# depth=10: hesap HER 10 sonuc icin ayri ucretlendirilir; 10 ustu ek maliyettir
# ve AIO kutusu zaten sayfanin en ustundedir, derinlik onu etkilemez.
_DEPTH = 10

# Es zamanli istek tavani. DataForSEO dakika basi limit uygular; 5 sorguyu
# ucerli dalgayla gondermek limite carpmadan gecikmeyi yariya indirir.
_CONCURRENCY = 3

_TIMEOUT = 90.0


def enabled() -> bool:
    """Kimlik yoksa ozellik yok sayilir (tarama etkilenmez)."""
    return bool(DFS_LOGIN and DFS_PASSWORD)


def _auth_header() -> str:
    raw = f"{DFS_LOGIN}:{DFS_PASSWORD}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def _collect_references(aio: dict) -> list[dict]:
    """
    AIO kaynaklari IKI yerde birden durabilir: kutunun kendi `references`
    dizisinde ve her `ai_overview_element`in ici referanslarinda/linklerinde.
    Ucunu de toplayip domain bazinda tekillestiriyoruz - yalniz ust seviyeye
    bakmak kaynaklarin bir kismini kaybettiriyor.
    """
    out: list[dict] = []
    seen: set[str] = set()

    def add(domain: str, url: str, title: str):
        d = (domain or sov._source_domain(url or "") or "").strip().lower()
        if not d or d in seen:
            return
        seen.add(d)
        out.append({"domain": d, "url": url or "", "title": title or ""})

    for ref in (aio.get("references") or []):
        if isinstance(ref, dict):
            add(ref.get("domain", ""), ref.get("url", ""), ref.get("title") or ref.get("source") or "")

    for el in (aio.get("items") or []):
        if not isinstance(el, dict):
            continue
        for ref in (el.get("references") or []):
            if isinstance(ref, dict):
                add(ref.get("domain", ""), ref.get("url", ""), ref.get("title") or ref.get("source") or "")
        for link in (el.get("links") or []):
            if isinstance(link, dict):
                add(link.get("domain", ""), link.get("url", ""), link.get("title", ""))
    return out


def _aio_text(aio: dict) -> str:
    """Kutunun tum metnini birlestirir (marka gecisi bu metinde aranir)."""
    parts: list[str] = []
    for el in (aio.get("items") or []):
        if isinstance(el, dict):
            for key in ("title", "text"):
                v = el.get(key)
                if isinstance(v, str) and v.strip():
                    parts.append(v.strip())
    return " ".join(parts)


def _parse_task(task: dict, name: str, own_domain: str) -> dict:
    """Tek sorgunun SERP yanitini AIO ozetine cevirir."""
    res = (task.get("result") or [{}])[0] or {}
    items = res.get("items") or []
    aio = next((i for i in items if isinstance(i, dict) and i.get("type") == "ai_overview"), None)

    if not aio:
        # Kutu hic cikmadi. Bu BASARISIZLIK DEGIL - bir olcumdur: Google bu
        # sorguda AI ozeti gostermiyor. Rapor bunu boyle sunmali.
        return {"present": False, "brand_mentioned": False, "cited_domains": [],
                "own_domain_cited": False, "text": "", "references": []}

    text = _aio_text(aio)
    refs = _collect_references(aio)
    domains = [r["domain"] for r in refs]

    return {
        "present": True,
        "asynchronous": bool(aio.get("asynchronous_ai_overview")),
        "brand_mentioned": sov._brand_mentioned(text, name) if text else False,
        "own_domain_cited": any(sov._is_own_domain(d, own_domain) for d in domains) if own_domain else False,
        "cited_domains": domains,
        "references": refs[:10],
        "text": text[:1200],
    }


async def _one_query(client: httpx.AsyncClient, sem: asyncio.Semaphore, query: str,
                     name: str, own_domain: str, lang: str) -> dict:
    body = [{
        "keyword": query,
        "location_code": _LOCATION.get(lang, _LOCATION["en"]),
        "language_code": lang if lang in _LOCATION else "en",
        "device": "desktop",
        "depth": _DEPTH,
        # Google AIO'yu cogu zaman ASENKRON yukler; bu bayrak olmadan kutu
        # yalnizca onbellekte varsa doner ve "AIO yok" diye YANLIS olceriz.
        "load_async_ai_overview": True,
    }]
    base = {"query": query, "present": False, "brand_mentioned": False,
            "own_domain_cited": False, "cited_domains": [], "references": [],
            "text": "", "cost": 0.0}
    last = {**base, "error": "unknown"}
    for attempt in (1, 2):
        try:
            async with sem:
                r = await client.post(f"https://{DFS_HOST}/v3/serp/google/organic/live/advanced",
                                      json=body, headers={"Authorization": _auth_header()},
                                      timeout=_TIMEOUT)
            if r.status_code != 200:
                logger.warning(f"AIO HTTP {r.status_code} (deneme {attempt}): {r.text[:200]}")
                last = {**base, "error": f"http_{r.status_code}"}
            else:
                data = r.json()
                task = (data.get("tasks") or [{}])[0] or {}
                # Ust seviye 20000 yeterli DEGIL: gorev seviyesi ayri hata dondurebilir
                # (olculdu 2026-08-02: ust seviye "Ok." iken gorev 40402 Invalid Path).
                code = task.get("status_code")
                if code == 20000:
                    return {**base, **_parse_task(task, name, own_domain),
                            "query": query, "cost": float(task.get("cost") or 0.0)}
                logger.warning(f"AIO gorev hatasi {code} (deneme {attempt}): {task.get('status_message')}")
                last = {**base, "error": f"task_{code}"}
                # 401xx saglayici tarafi gecici hatalardir (olculdu 2026-08-02:
                # 40101 "Internal SE Server Error"). 4xx yapilandirma hatasi ise
                # tekrar denemek bosuna gecikme ve maliyettir.
                if not (isinstance(code, int) and 40100 <= code < 40200):
                    return last
        except Exception as e:
            logger.warning(f"AIO sorgu hatasi ({query[:40]}, deneme {attempt}): {e}")
            last = {**base, "error": "exception"}
        if attempt == 1:
            await asyncio.sleep(2)
    return last


async def check_ai_overview(queries: list[str], name: str, own_domain: str = "",
                            lang: str = "tr") -> dict | None:
    """
    Verilen sorgular icin Google AI Overview kutusunu olcer.

    Doner:
      {
        "queries": [ {query, present, brand_mentioned, own_domain_cited,
                      cited_domains, references, text}, ... ],
        "aio_present_count": int,     # kac sorguda kutu cikti
        "aio_presence_rate": float,   # kutu cikma orani (0-1)
        "brand_mention_count": int,   # kutuda marka gecen sorgu sayisi
        "brand_mention_rate": float,  # kutu CIKAN sorgular icinde oran (0-1)
        "own_domain_cited_count": int,
        "top_cited_domains": [[domain, adet], ...],
        "cost_usd": float,
      }
    Kimlik yoksa ya da sorgu listesi bossa None doner (ozellik kapali).
    """
    if not enabled():
        return None
    qs = [q for q in (queries or []) if isinstance(q, str) and q.strip()][:8]
    if not qs:
        return None

    sem = asyncio.Semaphore(_CONCURRENCY)
    async with httpx.AsyncClient() as client:
        rows = await asyncio.gather(*[
            _one_query(client, sem, q, name, own_domain, lang) for q in qs
        ])

    # HATA != YOKLUK. Saglayici hatasi alan sorgu "AIO cikmadi" diye sayilirsa
    # varlik orani OLCULMEDEN dusuk raporlanir. Olculdu 2026-08-02: 5 sorgunun
    # biri 40101 "Internal SE Server Error" dondu ve oran 5/5 yerine 4/5 gorundu.
    # Hatali sorgu her iki paydadan da CIKARILIR, sayisi ayrica raporlanir.
    ok = [r for r in rows if not r.get("error")]
    failed = [r for r in rows if r.get("error")]
    present = [r for r in ok if r.get("present")]
    mentioned = [r for r in present if r.get("brand_mentioned")]
    domains = Counter(d for r in present for d in (r.get("cited_domains") or []))

    # Kaynak SAYFA TIPI dagilimi (golge mod, skoru DEGISTIRMEZ). Olculdu
    # 2026-08-02: AI Overview'in alintiladigi sayfalarin ~%49'u liste/
    # karsilastirma, yalnizca ~%5'i rehber — ve rehber GEONI'nin urettigi TEK
    # tipti. "Ne yapayim" sorusuna somut cevap: eksik SAYFA TIPI.
    # Ek maliyet YOK: veri zaten referanslardan geliyor.
    tip_dagilimi = None
    try:
        import source_type
        tip_dagilimi = source_type.dagilim(
            [r for row in present for r in (row.get("references") or [])])
    except Exception as e:
        logger.warning(f"Kaynak tipi dagilimi atlandi: {e}")

    return {
        "queries": rows,
        "source_types": tip_dagilimi,
        "queries_measured": len(ok),
        "queries_failed": len(failed),
        "aio_present_count": len(present),
        # Payda OLCULEBILEN sorgular: "Google bu kategoride ne siklikta AI ozeti
        # gosteriyor". Olculemeyeni paydaya koymak soruyu degistirir.
        "aio_presence_rate": round(len(present) / len(ok), 3) if ok else 0.0,
        "brand_mention_count": len(mentioned),
        # Payda yalniz KUTU CIKAN sorgular: kutunun cikmadigi sorguda gecmemek
        # bir basarisizlik degil, olcum firsatinin hic dogmamasidir.
        "brand_mention_rate": round(len(mentioned) / len(present), 3) if present else 0.0,
        "own_domain_cited_count": sum(1 for r in present if r.get("own_domain_cited")),
        "top_cited_domains": domains.most_common(10),
        "cost_usd": round(sum(float(r.get("cost") or 0.0) for r in rows), 5),
    }
