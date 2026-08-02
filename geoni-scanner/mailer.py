"""
GEONI Scanner - Email Notification Service
Sends the completed AI Visibility audit report to the user's email via Resend.
Fails silently (logs a warning) if RESEND_API_KEY is missing or the call errors,
so email delivery issues never block or fail the audit job itself.
"""

import os
import html
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

# E-posta gövdesi HTML. Dinamik degerler (domain, LLM konu basliklari, SOV
# sorgulari, bilet target'i, watchlist label) buradan gecmeden gomulmesin —
# aksi halde taranan siteden/kullanicidan gelen HTML enjekte edilebiliyordu.
def esc(v) -> str:
    return html.escape(str(v if v is not None else ""))

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
        "sov_title": "AI Aramada Görünürlük",
        "sov_summary": "{m}/{q} kategori sorgusunda öneriliyorsunuz",
        "sov_sources_label": "AI yanıtlarında kaynak gösterilen siteler:",
        "smoothed_label": "Yumuşatılmış skor (son 3 tarama)",
        "breakdown_labels": {
            # Insan dilinde (2026-08-02): web ResultsPage BREAKDOWN_LABELS_TR
            # ve mobil lib/i18n ile AYNI olmali — ayni skor uc yuzeyde farkli
            # adla anilirsa kullanici iki ayri sey sanar.
            "index_coverage": "AI'ın Görebildiği Sayfalar",
            "authority": "Güvenilirlik Sinyali",
            "freshness": "İçerik Güncelliği",
            "schema": "Sitenin AI'a Doğru Anlatılması",
            "ai_access": "AI Botlarının Erişimi",
            "engagement": "Etkileşim",
            "brand_recall": "Marka Bilinirliği",
            # Kisi/marka/sosyal taramanin kirilimi (brand_recall.score_breakdown).
            # Bu satirlar olmadan e-postada ham anahtar ("yanit_kalitesi") gorunurdu.
            "claude": "Claude",
            "chatgpt": "ChatGPT",
            "gemini": "Gemini",
            "perplexity": "Perplexity",
            "yanit_kalitesi": "Yanıt Kalitesi",
            "konu_uyumu": "Konu Uyumu",
            "kategori_gorunurlugu": "Kategori Görünürlüğü",
        },
        # Kisi/marka/sosyal raporu (alan adi yerine AD taranir)
        "brand_intro": "{date} tarihinde başlattığınız AI görünürlük taraması tamamlandı.",
        "brand_scanned": "Taranan",
        "brand_subject": "{domain} için AI Görünürlük Skoru: {score}/100",
        "brand_footer_note": 'Bu e-posta, <strong style="color:#64748B;">{domain}</strong> için başlattığınız AI görünürlük taramasının sonucudur.',
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
        "sov_title": "Visibility in AI Search",
        "sov_summary": "{m}/{q} category queries recommend you",
        "sov_sources_label": "Sites cited as sources in AI answers:",
        "smoothed_label": "Smoothed score (last 3 scans)",
        "breakdown_labels": {
            "index_coverage": "Pages AI Can Actually See",
            "authority": "Trust Signal",
            "freshness": "Content Freshness",
            "schema": "Correct Site Understanding by AI",
            "ai_access": "AI Bot Access",
            "engagement": "Engagement",
            "brand_recall": "Brand Recall",
            "claude": "Claude",
            "chatgpt": "ChatGPT",
            "gemini": "Gemini",
            "perplexity": "Perplexity",
            "yanit_kalitesi": "Answer Quality",
            "konu_uyumu": "Topic Relevance",
            "kategori_gorunurlugu": "Category Visibility",
        },
        "brand_intro": "The AI visibility scan you started on {date} is complete.",
        "brand_scanned": "Scanned",
        "brand_subject": "AI Visibility Score for {domain}: {score}/100",
        "brand_footer_note": 'This email is the result of the AI visibility scan you started for <strong style="color:#64748B;">{domain}</strong>.',
    },
}


def _format_datetime(iso_str: str | None, lang: str = "tr") -> str:
    """'3 Temmuz 2026, 15:30' / 'July 3, 2026, 15:30' bicimine cevirir; ayristirilamazsa simdiki zamani kullanir.
    Sunucu UTC calisir; kullaniciya Turkiye saatiyle gosterilir (naive degerler UTC varsayilir)."""
    try:
        dt = datetime.fromisoformat(iso_str) if iso_str else datetime.now(timezone.utc)
    except ValueError:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # 2026-07-23: container'da IANA tzdata YOKSA ZoneInfo firlatir ve bu, e-posta
    # gonderen tamamlanma-adimini -> TUM tarama'yi "failed" yapiyordu (skor hesaplanip
    # kaydedilmis olsa BILE). E-posta bir yan etki; ASLA taramayi dusurmemeli.
    # tzdata paketi eklendi (requirements) ama yine de +03:00 sabit fallback ile korunur.
    try:
        dt = dt.astimezone(ZoneInfo("Europe/Istanbul"))
    except Exception:
        dt = dt.astimezone(timezone(timedelta(hours=3)))  # TR sabit UTC+3
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
        items += f'<li style="margin-bottom:8px;color:#F1F5F9;">{esc(t.get("topic", ""))}</li>'
    return f'<ul style="padding-left:18px;margin:0;">{items}</ul>'


def _build_report_html(domain: str, result: dict, lang: str = "tr", brand: bool = False) -> str:
    """`brand=True`: kisi/marka/sosyal raporu. Sablon AYNI kalir (tek bakim
    noktasi); yalnizca 'alan adi' dili 'taranan ad' diline cevrilir ve konu
    listeleri brand payload'inin adlariyla okunur."""
    text = _EMAIL_TEXT.get(lang, _EMAIL_TEXT["tr"])
    if brand:
        text = {**text, "intro": text["brand_intro"],
                "scanned_domain": text["brand_scanned"],
                "footer_note": text["brand_footer_note"]}
    score = result.get("score", 0)
    color = _score_color(score)
    breakdown = result.get("breakdown") or result.get("score_breakdown") or {}
    # Web payload'i top_topics/opportunities, brand payload'i performing_topics/
    # opportunity_topics kullaniyor -- ikisini de kabul et.
    top_topics = result.get("top_topics") or result.get("performing_topics") or []
    opportunities = result.get("opportunities") or result.get("opportunity_topics") or []
    formatted_date = _format_datetime(result.get("created_at"), lang)

    breakdown_labels = text["breakdown_labels"]
    breakdown_rows = ""
    for key, value in breakdown.items():
        label = breakdown_labels.get(key, key)
        breakdown_rows += f"""
        <tr>
          <td style="padding:6px 0;color:#94A3B8;font-size:13px;">{esc(label)}</td>
          <td style="padding:6px 0;color:#F1F5F9;font-size:13px;text-align:right;">{esc(value)}</td>
        </tr>
        """

    # SOV bolumu (v3): kategori sorgularinda gorunurluk + kaynak siteler
    sov = result.get("sov") or {}
    sov_html = ""
    if sov.get("checked") and sov.get("score") is not None:
        m, q = sov.get("mention_count", 0), sov.get("query_count", 0)
        sov_color = "#3FB950" if (q and m / q >= .65) else "#D29922" if (q and m / q >= .4) else "#F85149"
        rows = ""
        for item in (sov.get("queries") or [])[:5]:
            mark = "✓" if item.get("mentioned") else "✗"
            mark_color = "#3FB950" if item.get("mentioned") else "#F85149"
            qtext = esc(str(item.get("query", ""))[:110])
            rows += (f'<tr><td style="padding:4px 8px 4px 0;color:{mark_color};font-size:13px;'
                     f'vertical-align:top;">{mark}</td>'
                     f'<td style="padding:4px 0;color:#94A3B8;font-size:13px;">{qtext}</td></tr>')
        sources = [esc(src.get("domain", "")) for src in (sov.get("sources") or [])[:5] if src.get("domain")]
        sources_html = ""
        if sources:
            sources_html = (f'<p style="color:#64748B;font-size:12px;margin:10px 0 0;">'
                            f'{text["sov_sources_label"]} '
                            f'<span style="color:#94A3B8;">{", ".join(sources)}</span></p>')
        sov_html = f"""
          <h2 style="color:#FFFFFF;font-size:16px;margin:24px 0 8px;">{text["sov_title"]}</h2>
          <p style="color:{sov_color};font-size:14px;font-weight:bold;margin:0 0 8px;">
            {text["sov_summary"].format(m=m, q=q)}
          </p>
          <table style="width:100%;border-collapse:collapse;">{rows}</table>
          {sources_html}
        """

    # Yumusatilmis skor satiri (stability, v3)
    stability = result.get("stability") or {}
    smoothed_html = ""
    if stability.get("smoothed_score") is not None:
        smoothed_html = (f'<div style="color:#64748B;font-size:12px;margin-top:6px;">'
                         f'{text["smoothed_label"]}: '
                         f'<span style="color:#94A3B8;font-weight:bold;">{stability["smoothed_score"]}</span></div>')

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
            <div style="color:#FFFFFF;font-size:18px;font-weight:bold;">{esc(domain)}</div>
          </div>

          <div style="text-align:center;margin-bottom:24px;">
            <div style="font-size:48px;font-weight:bold;color:{color};">{score}</div>
            <div style="color:#8893AB;font-size:12px;letter-spacing:1px;text-transform:uppercase;">{text["score_label"]}</div>
            {smoothed_html}
          </div>

          <table style="width:100%;border-collapse:collapse;margin-bottom:24px;">
            {breakdown_rows}
          </table>

          {sov_html}

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
            {text["footer_note"].format(domain=esc(domain))}
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


# Web istemcisi kisi/marka taramasinda adres yoksa BU yer tutucuyu gonderiyor
# (App.jsx: `payload.email || user?.email || 'anonymous@geoni.ai'`). Gercek bir
# kutu degil; rapor postasi buraya gitmemeli.
ANONIM_EPOSTA_YERTUTUCU = "anonymous@geoni.ai"


async def rapor_adresi(istek_eposta: "str | None", user_id: "str | None") -> str:
    """Rapor e-postasinin gidecegi adres.

    Mobil kisi/marka taramasi govdede HIC e-posta gondermiyor (giris zorunlu
    oldugu icin gerek gorulmemis), web ise adres yoksa yer tutucu koyuyor.
    Ikisinde de hesabin auth adresine dusulur -- yoksa "sonucu e-postana
    gondereceğiz" sozu sessizce tutulmazdi.
    """
    adres = (istek_eposta or "").strip()
    if adres and adres.lower() != ANONIM_EPOSTA_YERTUTUCU:
        return adres
    if not user_id:
        return ""
    # Tembel import: db.py dosya sonunda mailer'i import ediyor (dairesel olmasin).
    from db import get_auth_email
    return (await get_auth_email(user_id) or "").strip()


async def send_brand_report_email(to_email: str, name: str, result: dict, lang: str = "tr") -> bool:
    """Kisi/marka/sosyal tarama raporunu e-postayla gonderir.

    NEDEN VAR (2026-07-30): hem mobil (`res_scan_timeout`, `res_email_note`) hem
    web (`error_query_timeout`) "sonucu e-postana gondereceğiz" DIYORDU, ama bu
    e-posta yalnizca WEB (site) taramasi icin gonderiliyordu -- kisi/marka/sosyal
    taramada hicbir posta cikmiyordu. Yani 4 tarama tipinin 3'unde tutulamayan bir
    soz veriliyordu. Sozu kaldirmak yerine DOGRU yapiliyor: bekleme suresi
    dakikalar surdugu icin "kapat, sana yollariz" gercek bir deger.

    send_audit_report_email gibi ASLA firlatmaz -> cagiran ates-et-unut kullanir.
    """
    if not to_email:
        return False
    if not RESEND_API_KEY or RESEND_API_KEY == "your-resend-key-here":
        logger.warning("RESEND_API_KEY not configured, skipping brand email send")
        return False

    text = _EMAIL_TEXT.get(lang, _EMAIL_TEXT["tr"])
    html = _build_report_html(name, result, lang, brand=True)
    score = result.get("score", 0)

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}",
                         "Content-Type": "application/json"},
                json={"from": FROM_EMAIL, "to": [to_email],
                      "subject": text["brand_subject"].format(domain=name, score=score),
                      "html": html},
                timeout=15,
            )
            if resp.status_code in (200, 201):
                logger.info(f"Brand report email sent to {to_email} for '{name}'")
                return True
            logger.warning(f"Resend API error {resp.status_code}: {resp.text[:300]}")
            return False
    except Exception as e:
        logger.warning(f"Failed to send brand report email: {e}")
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


async def send_purchase_email(to_email: str, credits: int, amount: float, currency: str) -> bool:
    """Polar'in siparis onay maili kapali (org ayari) - onay mailini GEONI
    markasiyla biz gonderiyoruz. Kullanici dili profilde tutulmadigindan
    TR ana metin + tek satir EN ozet. Fatura Polar musteri portalinda."""
    cur = (currency or "USD").upper()
    return await send_ticket_email(
        to_email,
        f"Siparişiniz alındı — {credits} token yüklendi 🎉",
        "Ödemeniz tamamlandı",
        [
            f"<strong style=\"color:#EDEFF5\">{credits} token</strong> GEONI cüzdanınıza yüklendi. "
            f"Ödenen tutar: <strong style=\"color:#EDEFF5\">{amount:.2f} {cur}</strong>.",
            "Faturanız ödeme sağlayıcımız Polar tarafından düzenlenir; makbuz e-postanıza ayrıca iletilir.",
            f"<em>Payment received — {credits} tokens were added to your GEONI wallet.</em>",
        ],
        cta_label="Cüzdanı Aç",
        cta_url="https://app.geoni.ai",
    )


async def send_refund_email(to_email: str, credits: int) -> bool:
    """Admin iadesi sonrasi bilgilendirme; odeme iadesini Polar kendi
    kanalindan ayrica bildirir."""
    return await send_ticket_email(
        to_email,
        f"İadeniz tamamlandı — {credits} token düşüldü",
        "İade işleminiz tamamlandı",
        [
            f"Satın almanız iade edildi: ödemeniz Polar üzerinden kartınıza geri gönderildi, "
            f"<strong style=\"color:#EDEFF5\">{credits} token</strong> cüzdanınızdan düşüldü.",
            "Tutarın kartınıza yansıması bankanıza bağlı olarak birkaç iş günü sürebilir.",
            "<em>Your refund is complete — the payment was returned via Polar and the tokens were removed from your wallet.</em>",
        ],
        cta_label="Cüzdanı Aç",
        cta_url="https://app.geoni.ai",
    )


async def send_retention_warning_email(to_email: str, ref_code: str, days: int) -> bool:
    """(B) Bilet eklerinin `days` gün sonra kalıcı silineceğini bildirir —
    müşteri gerekirse şimdi indirir (KVKK veri-minimizasyonu + ödenen teslimatı korur)."""
    return await send_ticket_email(
        to_email,
        f"Dosya hatırlatması — {ref_code} ekleri {days} gün sonra silinecek",
        "Bilet dosyalarınız yakında kaldırılacak",
        [
            f"<strong style=\"color:#EDEFF5\">{ref_code}</strong> numaralı tamamlanmış biletinizin "
            f"dosyaları (ekler ve teslim edilen çıktılar) <strong style=\"color:#EDEFF5\">{days} gün</strong> "
            f"sonra kalıcı olarak silinecek.",
            "Gerekli dosyaları kaybetmemek için lütfen bu süre içinde indirin. Bu, verinizi "
            "gereğinden uzun saklamamak için uygulanan otomatik bir temizliktir.",
            "<em>Your completed ticket's files will be permanently removed soon — please download anything you still need.</em>",
        ],
        cta_label="Bilete Git",
        cta_url="https://app.geoni.ai/dashboard?tab=my_tickets",
    )


async def send_low_balance_alert_email(to_email: str, providers: list, threshold: float) -> bool:
    """(D) Prepaid sağlayıcı bakiyesi eşiğin altına düşünce admin'e uyarı.
    providers: [{provider, remaining, topups, spend}]."""
    def _sym(cur):
        return "₺" if (cur or "USD") == "TRY" else "$"
    rows = []
    for p in providers:
        s = _sym(p.get("currency"))
        usd_note = ""
        if p.get("currency") == "TRY" and p.get("remaining_usd") is not None:
            usd_note = f" (≈${p['remaining_usd']:.2f})"
        rows.append(
            f"<strong style=\"color:#EDEFF5\">{p['provider']}</strong>: kalan "
            f"<strong style=\"color:#F5A97C\">{s}{p['remaining']:.2f}</strong>{usd_note} "
            f"(yüklenen {s}{p.get('topups', 0):.2f} − harcanan {s}{p.get('spend', 0):.2f})"
        )
    return await send_ticket_email(
        to_email,
        f"⚠️ Düşük bakiye — {len(providers)} sağlayıcı ${threshold:.0f} altında",
        "Prepaid sağlayıcı bakiyesi kritik",
        [
            f"Aşağıdaki önden-yüklemeli hesapların kalan bakiyesi <strong style=\"color:#EDEFF5\">"
            f"${threshold:.0f}</strong> eşiğinin altına düştü — servis kesintisini önlemek için yükleme yapın:",
            *rows,
            "<em>Prepaid provider balance is low — top up to avoid service interruption.</em>",
        ],
        cta_label="Admin Paneli",
        cta_url="https://app.geoni.ai/admin",
    )


async def send_monitor_email(to_email: str, label: str, old_score: int, new_score: int) -> bool:
    """
    Izleme v2: duzenli otomatik taramada skor anlamli degistiginde (±5)
    kullaniciya bildirim. send_ticket_email ile ayni gorsel dil; fail-silent.
    """
    delta = new_score - old_score
    arrow = "▲" if delta > 0 else "▼"
    yon = "yükseldi" if delta > 0 else "düştü"
    subject = f"GEONI İzleme: {label} skoru {old_score}→{new_score} {arrow}"
    label_e = esc(label)  # HTML gövdesine ham gomulmesin (watchlist label kullanici girdisi)
    return await send_ticket_email(
        to_email,
        subject,
        f"{label_e} için AI görünürlük skoru {yon}",
        [
            f"Düzenli otomatik taramada <strong style=\"color:#EDEFF5\">{label_e}</strong> skoru "
            f"<strong style=\"color:#EDEFF5\">{old_score}</strong> → "
            f"<strong style=\"color:{'#3FB950' if delta > 0 else '#F85149'}\">{new_score}</strong> olarak ölçüldü ({arrow}{abs(delta)} puan).",
            "Raporun tamamını ve değişen boyutları panelinizde görebilirsiniz.",
        ],
        cta_label="Raporu Gör",
        cta_url="https://app.geoni.ai/dashboard",
    )
