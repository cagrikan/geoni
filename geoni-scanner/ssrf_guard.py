"""
GEONI - SSRF korumasi.

Tarama hedefi kullanicidan gelir (AuditRequest.domain) ve crawler bu host'a
HTTP istegi atar. Korumasiz birakilirsa saldirgan `domain` yerine ic bir
adres verip (localhost, 169.254.170.2 ECS metadata, 10.x/192.168.x ic
servisler, ::1 ...) sunucuyu kendi ic aglarina istek atmaya zorlayabilir
(SSRF) — is metadata/kimlik bilgisi sizintisina kadar gidebilir.

`assert_public_host` host'u cozer ve cozulen TUM IP'ler public degilse
reddeder. DNS cozumu bloklayici oldugu icin async baglamda
`await asyncio.to_thread(assert_public_host, host)` ile cagrilmali.
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

# Cozumden bagimsiz, isim bazli kara liste (bazilari DNS'e cikmadan yakalanir)
_BLOCKED_NAMES = {"localhost", "metadata", "metadata.google.internal"}
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal")


class BlockedHostError(ValueError):
    """Hedef host ic/ozel/ayrilmis bir adrese cozuluyor — tarama reddedildi."""


def _is_public_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    # Ozel, loopback, link-local (169.254/fe80), multicast, ayrilmis,
    # unspecified (0.0.0.0/::) ve IPv4-mapped IPv6 ic adresler.
    if (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
        return False
    if getattr(ip, "ipv4_mapped", None):
        return _is_public_ip(str(ip.ipv4_mapped))
    return True


def assert_public_host(host: str) -> None:
    """host public bir adrese cozulmuyorsa BlockedHostError firlatir.
    Sadece cikPlak hostname bekler (normalize_domain sonrasi)."""
    h = (host or "").strip().strip(".").lower()
    if not h:
        raise BlockedHostError("bos host")
    # Koseli parantezli IPv6 literal ([::1], [::ffff:169.254.170.2]) — getaddrinfo
    # parantezi cozemeyip fail-open'a dusuyordu ama HTTP istemcisi baglanabiliyor.
    # Parantezi soy ve varsa zone/port'u ayikla.
    if h.startswith("[") and "]" in h:
        h = h[1:h.index("]")]
    if h in _BLOCKED_NAMES or h.endswith(_BLOCKED_SUFFIXES):
        raise BlockedHostError(f"engellenen host adi: {host}")

    # Once IP literali mi diye dogrudan dene (::ffff:x, IPv6, IPv4). getaddrinfo'ya
    # birakmak koseli/mapped formlarda baypasa yol aciyordu.
    try:
        ipaddress.ip_address(h.split("%")[0])  # zone id'yi ayikla
        is_ip_literal = True
    except ValueError:
        is_ip_literal = False
    if is_ip_literal:
        if not _is_public_ip(h.split("%")[0]):
            raise BlockedHostError(f"host ic IP literali: {host}")
        return  # gecerli public IP literali

    # Legacy IPv4 kodlamalari: octal (0177.0.0.1), decimal (2130706433),
    # hex (0x7f.0.0.1). ipaddress bunlari reddeder, ama inet_aton cozer ve
    # HTTP istemcileri de cogu zaman baglanir. Platformdan bagimsiz yakala.
    try:
        ipv4 = socket.inet_ntoa(socket.inet_aton(h))
    except OSError:
        ipv4 = None
    if ipv4 is not None:
        if not _is_public_ip(ipv4):
            raise BlockedHostError(f"host legacy-kodlu ic IPv4: {host}")
        return

    # Tum A/AAAA kayitlarini coz; herhangi biri public degilse tumden reddet
    # (DNS rebinding'e karsi: bir kaydin bile ic olmasi yeter).
    try:
        infos = socket.getaddrinfo(h, None)
    except socket.gaierror:
        # Cozulemeyen host zaten crawl edilemez; ic sizinti riski yok — gecir,
        # crawler kendi hata yolunda 'failed' isaretler.
        return
    addrs = {info[4][0] for info in infos}
    if not addrs:
        return
    bad = [a for a in addrs if not _is_public_ip(a)]
    if bad:
        raise BlockedHostError(f"host ic adrese cozuluyor ({host} -> {', '.join(sorted(bad))})")


async def safe_get(client, url: str, *, max_redirects: int = 3, **kwargs):
    """SSRF-guvenli GET.

    ⚠️ BILINEN SINIR — TOCTOU / DNS-rebinding (kor denetim 2026-08-04, ACIK):
    assert_public_host host'u cozup "public" dogrulamasi yapip BIRAKIR; asil
    baglantiyi httpx (ve crawler tarafinda Chromium) KENDI cozumuyle kurar.
    Arada dusuk TTL'li kotu niyetli bir DNS kaydi (ilk sorguda public IP, hemen
    ardindan 127.0.0.1 / 169.254.169.254) devreye girerse guard gecilip ic
    adrese istek atilabilir. Kod icinde octal/hex/decimal IPv4, IPv6 literal ve
    redirect-hop bypass'lari kapatilmistir; kapatilmayan tek kategori budur.
    Istismari saldirganin kendi DNS altyapisini hassas zamanlamayla kontrol
    etmesini gerektirir (kolay degil, ama otomatiklestirilebilir).

    KALICI COZUM (yapilmadi, bilincli): cozulen IP'yi pinleyen ozel bir httpx
    transport + Playwright tarafinda uygulama katmaninda DNS sabitleme. URL'in
    host'unu IP ile degistirmek YETMEZ — TLS SNI ve sanal barindirma bozulur,
    sertifika dogrulamasi basarisiz olur. Yarim bir yama crawl'in tamamini
    riske atar; bu yuzden sinir BELGELENDI, yamanmadi. follow_redirects KAPALI tutulur; her 3xx yanitinda
    Location host'u assert_public_host ile dogrulanip elle takip edilir (en cok
    max_redirects hop). Boylece apex<->www gibi MESRU kanonik redirect'ler
    korunurken (robots/sitemap/llms sinyalleri kaybolmaz) 30x ile ic adrese
    (169.254.x, 10.x, metadata) sicrama engellenir. httpx'in kor
    follow_redirects=True'su bu ara host'lari dogrulamadigi icin kullanilamaz."""
    kwargs.pop("follow_redirects", None)
    current = url
    resp = None
    for _ in range(max_redirects + 1):
        resp = await client.get(current, follow_redirects=False, **kwargs)
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("location")
            if not loc:
                return resp
            nxt = urljoin(current, loc)
            host = urlparse(nxt).hostname or ""
            await asyncio.to_thread(assert_public_host, host)  # her hop dogrulanir
            current = nxt
            continue
        return resp
    return resp  # cok fazla redirect: son yaniti dondur
