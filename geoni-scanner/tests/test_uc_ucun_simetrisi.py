"""Üç tarama ucu AYNI korumaları taşır — asimetri = kusur.

NEDEN VAR (2026-08-08'de ÜÇ kusur üst üste bu asimetriden çıktı, üçü de canlıydı
ve üçünü de kurucu buldu):

1. **Turnstile mobil muafiyeti** — web ve markada VARDI, sosyalde YOKTU.
   Mobilde Turnstile widget'ı olmadığı için istek jetonsuz gidiyor; IP başına
   2'lik grace dolunca sosyal sekmesi KALICI 403 veriyordu.
2. **IP muafiyetine kimlik** — web `user_id_rl`, marka `user_id_rl2`, sosyal
   **sabit `None`**. Giriş yapmış mobil kullanıcı muafiyeti alamıyordu; CGNAT
   yüzünden başkasının taraması onu 429'a sokuyordu.
3. **`job_id` üretim sırası** — marka ve sosyalde ücretsiz-hak kapısı `job_id`
   tanımlanmadan kullanıyordu (`UnboundLocalError` → 500 → "Load failed"),
   web'de doğruydu.

Ortak desen: **bir koruma iki uçta düzeltilip üçüncüsünde unutuluyor.** Üç uç
çalışıyor göründüğü için de gözden kaçıyor. Bu test asimetriyi mekanik olarak
yakalar; yeni bir koruma eklenince üç uca da eklendiğini zorlar.

🪤 `main`i IMPORT ETMEZ — fastapi zinciri CI'ın asgari ortamında yok.
"""
import re
from pathlib import Path

SATIRLAR = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8").split("\n")

UCLAR = ("start_audit", "start_brand_check", "start_social_check")

KONTROLLER = {
    "auth 401": r"_login_required_message",
    "askiya alinmis hesap": r"is_user_suspended",
    "hiz siniri": r"enforce_audit_rate_limits",
    "IP muafiyeti": r"skip_ip=await _mobile_ip_exempt",
    "turnstile": r"enforce_turnstile",
    "turnstile mobil muafiyeti": r"not await _mobile_exempt",
    "ucretsiz hak kapisi": r"free_scan_gate",
    "bekleyen hak": r"_bekleyen_hak\[job_id\]",
    "ic tarama muafiyeti": r"_is_internal_scan",
}


def _bloklar():
    yerler = {}
    for i, ln in enumerate(SATIRLAR):
        m = re.match(r"async def (%s)\(" % "|".join(UCLAR), ln)
        if m:
            yerler[m.group(1)] = i
    sirali = sorted(yerler.items(), key=lambda kv: kv[1])
    out = {}
    for idx, (ad, bas) in enumerate(sirali):
        son = sirali[idx + 1][1] if idx + 1 < len(sirali) else bas + 260
        out[ad] = "\n".join(SATIRLAR[bas:son])
    return out


def test_uc_ucun_hepsi_BULUNDU():
    b = _bloklar()
    assert set(b) == set(UCLAR), f"uç bulunamadı: {set(UCLAR) - set(b)}"


def test_TUM_korumalar_UC_UCTA_DA_var():
    """🔴 Asıl kapan: bir uçta olup ötekinde olmayan koruma = canlı kusur."""
    b = _bloklar()
    eksikler = []
    for ad, desen in KONTROLLER.items():
        yok = [uc for uc in UCLAR if not re.search(desen, b[uc])]
        if yok and len(yok) < len(UCLAR):      # hiçbirinde yoksa o ayrı karar
            eksikler.append(f"{ad}: {', '.join(yok)}")
    assert not eksikler, "uçlar arasında asimetri:\n  " + "\n  ".join(eksikler)


def test_IP_muafiyetine_sabit_None_gecilmiyor():
    """Kimlik yerine `None` geçmek muafiyeti sessizce öldürür (2. kusur)."""
    kotu = [ln.strip() for ln in SATIRLAR
            if "_mobile_ip_exempt(" in ln and ln.strip().endswith("None))")]
    assert not kotu, kotu
