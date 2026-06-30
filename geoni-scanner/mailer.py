"""
GEONI Scanner - Email Notification Service
Sends the completed AI Visibility audit report to the user's email via Resend.
Fails silently (logs a warning) if RESEND_API_KEY is missing or the call errors,
so email delivery issues never block or fail the audit job itself.
"""

import os
import logging

import httpx

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL", "GEONI <rapor@geoni.ai>")

SCORE_COLOR = {
    "good": "#4ade80",
    "warn": "#FBBF24",
    "bad": "#f87171",
}


def _score_color(score: int) -> str:
    if score >= 65:
        return SCORE_COLOR["good"]
    if score >= 40:
        return SCORE_COLOR["warn"]
    return SCORE_COLOR["bad"]


def _render_topic_list(topics: list[dict], empty_text: str) -> str:
    if not topics:
        return f'<p style="color:#8893AB;font-size:14px;">{empty_text}</p>'
    items = ""
    for t in topics[:5]:
        items += f'<li style="margin-bottom:8px;color:#F1F5F9;">{t.get("topic", "")}</li>'
    return f'<ul style="padding-left:18px;margin:0;">{items}</ul>'


def _build_report_html(domain: str, result: dict) -> str:
    score = result.get("score", 0)
    color = _score_color(score)
    breakdown = result.get("breakdown") or result.get("score_breakdown") or {}
    top_topics = result.get("top_topics", [])
    opportunities = result.get("opportunities", [])

    breakdown_labels = {
        "index_coverage": "Dizin Kapsamı",
        "authority": "Otorite",
        "freshness": "Tazelik",
        "schema": "Şema Bütünlüğü",
        "engagement": "Etkileşim",
    }
    breakdown_rows = ""
    for key, value in breakdown.items():
        label = breakdown_labels.get(key, key)
        breakdown_rows += f"""
        <tr>
          <td style="padding:6px 0;color:#94A3B8;font-size:13px;">{label}</td>
          <td style="padding:6px 0;color:#F1F5F9;font-size:13px;text-align:right;">{value}</td>
        </tr>
        """

    return f"""
    <div style="background:#07070F;padding:32px 16px;font-family:Arial,sans-serif;">
      <div style="max-width:560px;margin:0 auto;background:#0E0E1C;border-radius:16px;overflow:hidden;border:1px solid rgba(129,140,248,0.2);">
        <div style="padding:24px 32px;border-bottom:1px solid rgba(129,140,248,0.15);">
          <span style="color:#818CF8;font-weight:bold;letter-spacing:2px;font-size:14px;">GEONI</span>
        </div>
        <div style="padding:32px;">
          <p style="color:#8893AB;font-size:13px;margin:0 0 4px;">{domain}</p>
          <h1 style="color:#FFFFFF;font-size:22px;margin:0 0 24px;">AI Görünürlük Raporu</h1>

          <div style="text-align:center;margin-bottom:24px;">
            <div style="font-size:48px;font-weight:bold;color:{color};">{score}</div>
            <div style="color:#8893AB;font-size:12px;letter-spacing:1px;text-transform:uppercase;">AI Görünürlük Skoru</div>
          </div>

          <table style="width:100%;border-collapse:collapse;margin-bottom:24px;">
            {breakdown_rows}
          </table>

          <h2 style="color:#FFFFFF;font-size:16px;margin:24px 0 12px;">Güçlü Olduğunuz Konular</h2>
          {_render_topic_list(top_topics, "Henüz güçlü bir konu tespit edilmedi.")}

          <h2 style="color:#FFFFFF;font-size:16px;margin:24px 0 12px;">Kaçırdığınız Fırsatlar</h2>
          {_render_topic_list(opportunities, "Fırsat alanı tespit edilmedi.")}
        </div>
      </div>
    </div>
    """


async def send_audit_report_email(to_email: str, domain: str, result: dict) -> bool:
    """
    Send the completed audit report via Resend. Returns True on success,
    False on any failure (auth missing, network error, API error) — never
    raises, so callers can fire-and-forget without try/except.
    """
    if not RESEND_API_KEY or RESEND_API_KEY == "your-resend-key-here":
        logger.warning("RESEND_API_KEY not configured, skipping email send")
        return False

    html = _build_report_html(domain, result)
    score = result.get("score", 0)

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": FROM_EMAIL,
                    "to": [to_email],
                    "subject": f"{domain} için AI Görünürlük Skorunuz: {score}/100",
                    "html": html,
                },
                timeout=15,
            )
            if resp.status_code in (200, 201):
                logger.info(f"Report email sent to {to_email} for {domain}")
                return True
            else:
                logger.warning(f"Resend API error {resp.status_code}: {resp.text[:300]}")
                return False
    except Exception as e:
        logger.warning(f"Failed to send report email: {e}")
        return False

