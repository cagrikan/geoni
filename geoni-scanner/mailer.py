"""
GEONI Scanner - Email Notification Service
Sends the completed AI Visibility audit report to the user's email via Resend.
Fails silently (logs a warning) if RESEND_API_KEY is missing or the call errors,
so email delivery issues never block or fail the audit job itself.
"""

import os
import logging
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL", "GEONI <rapor@geoni.ai>")

SCORE_COLOR = {
    "good": "#4ade80",
    "warn": "#FBBF24",
    "bad": "#f87171",
}

_TR_MONTHS = [
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
]


def _format_tr_datetime(iso_str: str | None) -> str:
    """'3 Temmuz 2026, 15:30' bicimine cevirir; ayristirilamazsa simdiki zamani kullanir."""
    try:
        dt = datetime.fromisoformat(iso_str) if iso_str else datetime.now()
    except ValueError:
        dt = datetime.now()
    return f"{dt.day} {_TR_MONTHS[dt.month - 1]} {dt.year}, {dt.strftime('%H:%M')}"


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
    formatted_date = _format_tr_datetime(result.get("created_at"))

    breakdown_labels = {
        "index_coverage": "Dizin Kapsamı",
        "authority": "Otorite",
        "freshness": "Tazelik",
        "schema": "Şema Bütünlüğü",
        "engagement": "Etkileşim",
        "brand_recall": "Marka Bilinirliği",
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
        <div style="padding:24px 32px;border-bottom:1px solid rgba(129,140,248,0.15);display:flex;justify-content:space-between;align-items:center;">
          <span style="color:#818CF8;font-weight:bold;letter-spacing:2px;font-size:14px;">GEONI</span>
          <span style="color:#64748B;font-size:12px;">{formatted_date}</span>
        </div>
        <div style="padding:32px;">
          <p style="color:#8893AB;font-size:13px;margin:0 0 20px;">
            {formatted_date} tarihinde talep ettiğiniz AI Görünürlük Taraması tamamlandı.
          </p>

          <div style="background:rgba(129,140,248,0.08);border:1px solid rgba(129,140,248,0.2);border-radius:10px;padding:14px 18px;margin:0 0 24px;">
            <div style="color:#64748B;font-size:11px;letter-spacing:1px;text-transform:uppercase;margin:0 0 4px;">Taranan Alan Adı</div>
            <div style="color:#FFFFFF;font-size:18px;font-weight:bold;">{domain}</div>
          </div>

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

          <div style="margin-top:32px;text-align:center;">
            <a href="https://app.geoni.ai" style="display:inline-block;background:#818CF8;color:#0D0D1A;padding:14px 28px;border-radius:8px;text-decoration:none;font-weight:bold;font-size:14px;">
              Tam Raporu Görüntüle
            </a>
          </div>
        </div>
        <div style="padding:20px 32px;border-top:1px solid rgba(129,140,248,0.15);text-align:center;">
          <p style="color:#64748B;font-size:11px;margin:0 0 4px;">GEONI — AI Görünürlük Platformu</p>
          <p style="color:#475569;font-size:11px;margin:0;">
            Bu e-posta, <strong style="color:#64748B;">{domain}</strong> için app.geoni.ai üzerinden başlattığınız
            ücretsiz AI görünürlük taraması sonucunda otomatik olarak gönderilmiştir.
          </p>
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
