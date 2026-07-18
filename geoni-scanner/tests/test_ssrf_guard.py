"""SSRF guard regresyon kilidi (denetim #9, en yüksek öncelik).

assert_public_host tarama hedefinin iç/özel/bulut-metadata adreslerine
çözülmesini engeller. Bu tablo, geçmişte bypass'a yol açan kodlama biçimlerini
(IPv6 mapped, köşeli parantez, legacy octal/decimal/hex IPv4) sabitler — biri
tekrar açılırsa test kırmızıya döner. Hepsi IP literali / isim tabanlı olduğu
için DNS/ağ GEREKMEZ.
"""
import pytest

from ssrf_guard import assert_public_host, _is_public_ip, BlockedHostError

# Reddedilmesi ZORUNLU hedefler (iç ağ / metadata / loopback / legacy kodlama).
BLOCKED = [
    "127.0.0.1",
    "0.0.0.0",
    "10.0.0.1",
    "192.168.1.1",
    "172.16.0.1",
    "169.254.170.2",        # ECS task metadata
    "169.254.169.254",      # bulut instance metadata
    "::1",
    "[::1]",                # köşeli parantezli IPv6 literal
    "fe80::1",              # link-local IPv6
    "[::ffff:169.254.170.2]",  # IPv4-mapped IPv6 -> link-local
    "2130706433",           # decimal 127.0.0.1 (inet_aton)
    "0x7f000001",           # hex 127.0.0.1
    "0177.0.0.1",           # octal ilk oktet -> 127
    "localhost",
    "metadata.google.internal",
    "foo.internal",
    "bar.local",
    "",                     # boş host
]

# Geçmesi gereken gerçek public IP literalleri.
PUBLIC = ["8.8.8.8", "1.1.1.1", "93.184.216.34"]


@pytest.mark.parametrize("host", BLOCKED)
def test_blocked_hosts_raise(host):
    with pytest.raises(BlockedHostError):
        assert_public_host(host)


@pytest.mark.parametrize("host", PUBLIC)
def test_public_ip_literals_pass(host):
    # Reddetmemeli (BlockedHostError fırlatmamalı).
    assert_public_host(host)


def test_is_public_ip_basic():
    assert _is_public_ip("8.8.8.8") is True
    assert _is_public_ip("10.0.0.1") is False
    assert _is_public_ip("169.254.170.2") is False
    assert _is_public_ip("::1") is False
    assert _is_public_ip("not-an-ip") is False
