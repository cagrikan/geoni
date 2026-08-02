"""Bilet otomasyonu SSRF kapisi (2026-08-02 guvenlik denetimi).

🪤 YASANDI (kod okumasiyla dogrulandi, canliya istek atilmadi):
`POST /api/tickets` body.target serbest string; `normalize_domain` yalniz
SOZDIZIMI dogrular ve "169.254.170.2" (ECS metadata adresi) regex'i GECER —
rakamlar da [a-z0-9] sinifindadir. `safe_get` ise yalniz REDIRECT hop'larini
dogrular, ILK istegi degil (ssrf_guard.py:98). Sonuc: kimligi dogrulanmis bir
kullanici bilet satin alarak sunucuya ic aga istek attirabiliyordu
(https://169.254.170.2/robots.txt, /sitemap.xml).

Bu test guard'in ISTEGI ATAN fonksiyonlarda durdugunu kanitlar — cagirana
guvenmez, cunku cagiran yarin degisir.
"""
import asyncio

import pytest

import ticket_automation as ta


IC_HEDEFLER = ["169.254.170.2", "127.0.0.1", "10.0.0.1", "192.168.1.1",
               "localhost", "0177.0.0.1", "2130706433"]


@pytest.mark.parametrize("hedef", IC_HEDEFLER)
def test_ic_hedef_reddedilir(hedef):
    assert asyncio.run(ta._hedef_public_mi(hedef)) is False, hedef


def test_normalize_domain_ham_IP_yi_GECIRIYOR():
    """Kok nedenin kaniti: sozdizimi kapisi tek basina YETMEZ.

    Bu test kirmizi olursa normalize_domain sertlestirilmis demektir — o zaman
    bile guard kalmali (savunma derinligi), ama bu satir guncellenmeli.
    """
    from db import normalize_domain
    assert normalize_domain("169.254.170.2") == "169.254.170.2"


def test_sitemap_arama_ic_hedefte_AG_ISTEGI_ATMAZ(monkeypatch):
    """Guard'in gercekten istegin ONUNDE oldugunu kanitlar: safe_get cagrilirsa
    test patlar (yani 'None dondu' yeterli kanit degil, cagri sayilir)."""
    cagrildi = []

    async def _patlat(*a, **k):
        cagrildi.append(a)
        raise AssertionError("safe_get ic hedefe cagrildi — SSRF acik")

    monkeypatch.setattr(ta, "safe_get", _patlat)
    assert asyncio.run(ta._find_sitemap("169.254.170.2", None)) is None
    assert cagrildi == []


def test_robots_uretimi_ic_hedefte_AG_ISTEGI_ATMAZ(monkeypatch):
    cagrildi = []

    async def _patlat(*a, **k):
        cagrildi.append(a)
        raise AssertionError("safe_get ic hedefe cagrildi — SSRF acik")

    monkeypatch.setattr(ta, "safe_get", _patlat)
    metin, durum, _ = asyncio.run(ta.generate_robots_txt("169.254.170.2"))
    assert cagrildi == []
    # Istek atilmadigi icin "mevcut robots cekilemedi" yolundan gecer.
    assert durum == "created" and metin


def test_public_hedef_normal_akista_GECER(monkeypatch):
    """Guard yanlis alarm uretmemeli: gercek bir domain istegi atabilmeli."""
    class _Yanit:
        status_code = 200
        text = "User-agent: *\nDisallow:\n"

    async def _sahte(*a, **k):
        return _Yanit()

    monkeypatch.setattr(ta, "safe_get", _sahte)
    _, durum, _ = asyncio.run(ta.generate_robots_txt("example.com"))
    assert durum != "created", "public hedefte canli robots.txt okunmali"
