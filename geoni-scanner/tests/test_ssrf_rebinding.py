"""DNS-rebinding (TOCTOU) savunması — kör denetim 2026-08-04'te açık bulundu.

SORUN: `assert_public_host` host'u çözüp "public" der ve BIRAKIR; asıl bağlantıyı
httpx (crawler'da Chromium) KENDİ çözümüyle kurar. Düşük TTL'li kötü niyetli bir
kayıt (ilk sorguda public IP, hemen ardından 127.0.0.1 / 169.254.169.254) araya
girerse guard geçilip iç adrese istek atılabiliyordu.

SAVUNMA İKİ KATMANLI:
1. httpx: bağlantı SONRASI gerçek peer IP doğrulanır; public değilse yanıt
   REDDEDİLİR. Yalnız GET yaptığımız için bu yeterli — veri çağırana ulaşmaz.
2. Chromium: `--host-resolver-rules` ile iç/loopback/metadata adreslerine çözüm
   tarayıcı seviyesinde yasaklanır; DNS ne dönerse dönsün oraya bağlanılmaz.
"""
from pathlib import Path

import pytest

import ssrf_guard as g

_KOK = Path(__file__).resolve().parent.parent


class _SahteStream:
    def __init__(self, ip):
        self._ip = ip

    def get_extra_info(self, anahtar):
        return (self._ip, 443) if anahtar == "server_addr" else None


class _SahteYanit:
    def __init__(self, ip=None):
        self.extensions = {"network_stream": _SahteStream(ip)} if ip else {}


@pytest.mark.parametrize("ip", [
    "127.0.0.1",            # loopback
    "169.254.169.254",      # bulut metadata
    "10.0.0.5",             # özel ağ
    "192.168.1.1",
    "172.16.0.1",
])
def test_ic_adrese_baglanti_reddedilir(ip):
    """Guard geçilse bile bağlantı iç adrese gittiyse yanıt reddedilmeli."""
    with pytest.raises(g.BlockedHostError):
        g._baglantiyi_dogrula(_SahteYanit(ip), "kotu.example")


def test_public_adres_gecer():
    g._baglantiyi_dogrula(_SahteYanit("104.20.23.154"), "example.com")  # patlamamalı


def test_olculemezse_fail_open():
    """network_stream yoksa (proxy/özel transport) güvenlik katmanı uygulamayı
    KIRMAMALI — rate-limit ile aynı ilke."""
    g._baglantiyi_dogrula(_SahteYanit(None), "example.com")  # patlamamalı


def test_safe_get_her_yanitta_dogruluyor():
    """Kaynak taraması: doğrulama çağrısı safe_get döngüsünde olmalı; yoksa
    redirect hop'larında rebinding yine geçer."""
    kaynak = (_KOK / "ssrf_guard.py").read_text(encoding="utf-8")
    assert "_baglantiyi_dogrula(resp" in kaynak


def test_chromium_dns_kurali_var():
    """Crawler tarafı: Chromium'a host-resolver kuralı verilmeli."""
    kaynak = (_KOK / "crawler.py").read_text(encoding="utf-8")
    assert "--host-resolver-rules=" in kaynak
    for hedef in ("169.254.169.254", "localhost", "metadata.google.internal"):
        assert hedef in kaynak, f"{hedef} çözüm yasağında yok"
