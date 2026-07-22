"""Admin panel Sentry ozeti — acik/cozulmus hata izleme (salt okuma).

SENTRY_AUTH_TOKEN env yoksa {configured: False} doner (deploy gecisinde/DSN
kurulu degilse cokmez). Sentry EU data region (DSN .de.sentry.io) API'sini
kullanir. Panelde sadece "acik / cozuldu + baslik + sayi" gosterilir; detay
gerekince permalink ile Sentry'ye gidilir.
"""
import os
import logging
import httpx

logger = logging.getLogger(__name__)

_HOST = "https://de.sentry.io/api/0"  # EU data region
_TIMEOUT = 8.0


def _headers() -> dict:
    return {"Authorization": f"Bearer {os.environ['SENTRY_AUTH_TOKEN'].strip()}"}


def _slim(issue: dict) -> dict:
    # Panelin ihtiyaci olan minimum alan (detay Sentry'de).
    return {
        "id": issue.get("id"),
        "title": issue.get("title"),
        "level": issue.get("level"),
        "status": issue.get("status"),        # unresolved | resolved | ignored
        "count": issue.get("count"),
        "userCount": issue.get("userCount"),
        "lastSeen": issue.get("lastSeen"),
        "permalink": issue.get("permalink"),
    }


async def get_sentry_summary(period: str = "14d") -> dict:
    """Acik (unresolved) issue listesi + son donem cozulen sayisi.
    Token yoksa configured:False; API hatasinda error alani ile bos-guvenli doner."""
    token = os.environ.get("SENTRY_AUTH_TOKEN", "").strip()
    if not token:
        return {"configured": False, "open": [], "open_count": 0, "resolved_count": 0}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_headers()) as c:
            orgs = (await c.get(f"{_HOST}/organizations/")).json()
            if not orgs:
                return {"configured": True, "open": [], "open_count": 0, "resolved_count": 0}
            org = orgs[0]["slug"]
            projs = (await c.get(f"{_HOST}/organizations/{org}/projects/")).json()
            if not projs:
                return {"configured": True, "open": [], "open_count": 0, "resolved_count": 0}
            proj = projs[0]["slug"]
            base = f"{_HOST}/projects/{org}/{proj}/issues/"
            open_r = await c.get(base, params={"query": "is:unresolved", "statsPeriod": period, "limit": 25})
            resolved_r = await c.get(base, params={"query": "is:resolved", "statsPeriod": period, "limit": 100})
            open_issues = [_slim(i) for i in open_r.json()]
            return {
                "configured": True,
                "org": org,
                "project": proj,
                "open": open_issues,
                "open_count": len(open_issues),
                "resolved_count": len(resolved_r.json()),
            }
    except Exception as e:
        logger.warning(f"get_sentry_summary error: {e}")
        return {"configured": True, "open": [], "open_count": 0, "resolved_count": 0, "error": "Sentry erişilemedi"}
