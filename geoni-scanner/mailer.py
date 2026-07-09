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
FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL", "GEONI <mail@geoni.ai>")

SCORE_COLOR = {
    "good": "#4ade80",
    "warn": "#FBBF24",
    "bad": "#f87171",
}

_TR_MONTHS = [
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
]

_EN_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

# E-posta raporu icin dil bazli tum sabit metinler (bkz. _build_report_html)
_EMAIL_TEXT = {
    "tr": {
        "intro": "{date} tarihinde talep ettiğiniz AI Görünürlük Taraması tamamlandı.",
        "scanned_domain": "Taranan Alan Adı",
        "score_label": "AI Görünürlük Skoru",
        "strong_topics": "Güçlü Olduğunuz Konular",
        "strong_topics_empty": "Henüz güçlü bir konu tespit edilmedi.",
        "missed_opportunities": "Kaçırdığınız Fırsatlar",
        "opportunities_empty": "Fırsat alanı tespit edilmedi.",
        "view_report": "Tam Raporu Görüntüle",
        "footer_brand": "GEONI — AI Görünürlük Platformu",
        "footer_note": 'Bu e-posta, <strong style="color:#64748B;">{domain}</strong> için app.geoni.ai üzerinden başlattığınız ücretsiz AI görünürlük taraması sonucunda otomatik olarak gönderilmiştir.',
        "subject": "{domain} için AI Görünürlük Skorunuz: {score}/100",
        "breakdown_labels": {
            "index_coverage": "Dizin Kapsamı",
            "authority": "Otorite",
            "freshness": "Tazelik",
            "schema": "Şema Bütünlüğü",
            "engagement": "Etkileşim",
            "brand_recall": "Marka Bilinirliği",
        },
    },
    "en": {
        "intro": "The AI Visibility Scan you requested on {date} is complete.",
        "scanned_domain": "Scanned Domain",
        "score_label": "AI Visibility Score",
        "strong_topics": "Topics You Own",
        "strong_topics_empty": "No strong topics detected yet.",
        "missed_opportunities": "Opportunities You're Missing",
        "opportunities_empty": "No opportunity areas detected.",
        "view_report": "View Full Report",
        "footer_brand": "GEONI — AI Visibility Platform",
        "footer_note": 'This email was sent automatically as a result of the free AI visibility scan you started for <strong style="color:#64748B;">{domain}</strong> on app.geoni.ai.',
        "subject": "Your AI Visibility Score for {domain}: {score}/100",
        "breakdown_labels": {
            "index_coverage": "Index Coverage",
            "authority": "Authority",
            "freshness": "Freshness",
            "schema": "Schema Integrity",
            "engagement": "Engagement",
            "brand_recall": "Brand Recall",
        },
    },
}


def _format_datetime(iso_str: str | None, lang: str = "tr") -> str:
    """'3 Temmuz 2026, 15:30' / 'July 3, 2026, 15:30' bicimine cevirir; ayristirilamazsa simdiki zamani kullanir."""
    try:
        dt = datetime.fromisoformat(iso_str) if iso_str else datetime.now()
    except ValueError:
        dt = datetime.now()
    if lang == "en":
        return f"{_EN_MONTHS[dt.month - 1]} {dt.day}, {dt.year}, {dt.strftime('%H:%M')}"
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


def _build_report_html(domain: str, result: dict, lang: str = "tr") -> str:
    text = _EMAIL_TEXT.get(lang, _EMAIL_TEXT["tr"])
    score = result.get("score", 0)
    color = _score_color(score)
    breakdown = result.get("breakdown") or result.get("score_breakdown") or {}
    top_topics = result.get("top_topics", [])
    opportunities = result.get("opportunities", [])
    formatted_date = _format_datetime(result.get("created_at"), lang)

    breakdown_labels = text["breakdown_labels"]
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
            {text["intro"].format(date=formatted_date)}
          </p>

          <div style="background:rgba(129,140,248,0.08);border:1px solid rgba(129,140,248,0.2);border-radius:10px;padding:14px 18px;margin:0 0 24px;">
            <div style="color:#64748B;font-size:11px;letter-spacing:1px;text-transform:uppercase;margin:0 0 4px;">{text["scanned_domain"]}</div>
            <div style="color:#FFFFFF;font-size:18px;font-weight:bold;">{domain}</div>
          </div>

          <div style="text-align:center;margin-bottom:24px;">
            <div style="font-size:48px;font-weight:bold;color:{color};">{score}</div>
            <div style="color:#8893AB;font-size:12px;letter-spacing:1px;text-transform:uppercase;">{text["score_label"]}</div>
          </div>

          <table style="width:100%;border-collapse:collapse;margin-bottom:24px;">
            {breakdown_rows}
          </table>

          <h2 style="color:#FFFFFF;font-size:16px;margin:24px 0 12px;">{text["strong_topics"]}</h2>
          {_render_topic_list(top_topics, text["strong_topics_empty"])}

          <h2 style="color:#FFFFFF;font-size:16px;margin:24px 0 12px;">{text["missed_opportunities"]}</h2>
          {_render_topic_list(opportunities, text["opportunities_empty"])}

          <div style="margin-top:32px;text-align:center;">
            <a href="https://app.geoni.ai" style="display:inline-block;background:#818CF8;color:#0D0D1A;padding:14px 28px;border-radius:8px;text-decoration:none;font-weight:bold;font-size:14px;">
              {text["view_report"]}
            </a>
          </div>
        </div>
        <div style="padding:20px 32px;border-top:1px solid rgba(129,140,248,0.15);text-align:center;">
          <p style="color:#64748B;font-size:11px;margin:0 0 4px;">{text["footer_brand"]}</p>
          <p style="color:#475569;font-size:11px;margin:0;">
            {text["footer_note"].format(domain=domain)}
          </p>
        </div>
      </div>
    </div>
    """


async def send_audit_report_email(to_email: str, domain: str, result: dict, lang: str = "tr") -> bool:
    """
    Send the completed audit report via Resend. Returns True on success,
    False on any failure (auth missing, network error, API error) — never
    raises, so callers can fire-and-forget without try/except.
    """
    if not RESEND_API_KEY or RESEND_API_KEY == "your-resend-key-here":
        logger.warning("RESEND_API_KEY not configured, skipping email send")
        return False

    text = _EMAIL_TEXT.get(lang, _EMAIL_TEXT["tr"])
    html = _build_report_html(domain, result, lang)
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
                    "subject": text["subject"].format(domain=domain, score=score),
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


# ── Bilet olay bildirimleri ──────────────────────────────────────────────
# Tek sablonlu, kisa islem maili: baslik + satirlar + app'e CTA. Rapor
# maili gibi fail-silent: cagiranlar asyncio.create_task ile atesleyip
# unutabilir, endpoint gecikmez, hata yanit akisini bozmaz.

_TICKET_MAIL_STYLE = "font-family:-apple-system,Segoe UI,sans-serif;background:#0A0B10;color:#EDEFF5;padding:32px;border-radius:14px;max-width:520px;margin:0 auto"


async def send_ticket_email(to_email: str, subject: str, heading: str, lines: list, cta_label: str = "Bilete Git", cta_url: str = "https://app.geoni.ai") -> bool:
    if not RESEND_API_KEY or RESEND_API_KEY == "your-resend-key-here":
        logger.warning("RESEND_API_KEY not configured, skipping ticket email")
        return False
    if not to_email:
        return False
    body_rows = "".join(
        f'<p style="margin:6px 0;font-size:14px;line-height:1.6;color:#9BA3B5;">{line}</p>'
        for line in lines
    )
    html = f"""
    <div style="{_TICKET_MAIL_STYLE}">
      <div style="font-size:16px;font-weight:600;letter-spacing:.1em;color:#7C86F5;margin-bottom:20px;">GEONI</div>
      <h2 style="font-size:18px;font-weight:600;margin:0 0 12px;color:#EDEFF5;">{heading}</h2>
      {body_rows}
      <a href="{cta_url}" style="display:inline-block;margin-top:20px;background:#7C86F5;color:#FFFFFF;padding:11px 22px;border-radius:6px;text-decoration:none;font-weight:600;font-size:14px;">{cta_label} →</a>
      <p style="margin:24px 0 0;font-size:12px;color:#697083;">Bu e-posta GEONI bilet sisteminden otomatik gönderilmiştir.</p>
    </div>
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
                json={"from": FROM_EMAIL, "to": [to_email], "subject": subject, "html": html},
                timeout=15,
            )
            if resp.status_code in (200, 201):
                logger.info(f"Ticket email sent to {to_email}: {subject}")
                return True
            logger.warning(f"Resend ticket email error {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        logger.warning(f"Failed to send ticket email: {e}")
        return False
