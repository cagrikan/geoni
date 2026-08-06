"""Var olmayan alan adı taranmaz — puan üretmez, kredi yakmaz.

NE OLDU (2026-08-06, CANLIDA ölçüldü — tahmin değil):
`bu-alan-yok-9x7z.invalid` taraması `status=complete`, `score=40`, `pages=0`
ile bitti. Yani DNS'te hiç var olmayan bir adres, kullanıcıya gerçekmiş gibi
duran bir puan gösterdi ve kredisini yaktı.

KÖK NEDEN: `ssrf_guard.assert_public_host` içindeki

    except socket.gaierror:
        # Cozulemeyen host zaten crawl edilemez ... crawler kendi hata yolunda
        # 'failed' isaretler.
        return

dalı. Yorumdaki varsayım YANLIŞ: crawler failed işaretlemiyor, 0 sayfayla
devam ediyor ve skorlama yine çalışıyor. Güvenlik dalı doğru (çözülemeyen
host'ta iç sızıntı riski yok), eksik olan VARLIK kontrolüydü.

Alan adını yanlış yazmak en sık kullanıcı hatası; ilk deneyimde olan bu.
"""
import socket

import pytest

import ssrf_guard


def test_cozulen_host_TRUE(monkeypatch):
    monkeypatch.setattr(ssrf_guard.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))])
    assert ssrf_guard.host_cozuluyor_mu("example.com") is True


def test_cozulmeyen_host_FALSE(monkeypatch):
    def patla(*a, **k):
        raise socket.gaierror("Name or service not known")
    monkeypatch.setattr(ssrf_guard.socket, "getaddrinfo", patla)
    monkeypatch.setattr(ssrf_guard.time, "sleep", lambda s: None)
    assert ssrf_guard.host_cozuluyor_mu("bu-alan-yok-9x7z.invalid") is False


def test_GECICI_dns_aksakligi_gercek_siteyi_REDDETMEZ(monkeypatch):
    """🪤 Tek denemede karar verilseydi geçici aksaklık gerçek siteyi eleyecekti."""
    cagri = {"n": 0}

    def bazen(*a, **k):
        cagri["n"] += 1
        if cagri["n"] == 1:
            raise socket.gaierror("temporary failure")
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(ssrf_guard.socket, "getaddrinfo", bazen)
    monkeypatch.setattr(ssrf_guard.time, "sleep", lambda s: None)
    assert ssrf_guard.host_cozuluyor_mu("example.com") is True
    assert cagri["n"] == 2


def test_ag_hatasi_kullaniciyi_SUCLAMAZ(monkeypatch):
    """gaierror değil OSError = bizim ağ katmanımız. Kullanıcının alanı suçlanmaz."""
    def patla(*a, **k):
        raise OSError("network unreachable")
    monkeypatch.setattr(ssrf_guard.socket, "getaddrinfo", patla)
    assert ssrf_guard.host_cozuluyor_mu("example.com") is True


def test_bos_girdi_FALSE():
    assert ssrf_guard.host_cozuluyor_mu("") is False
    assert ssrf_guard.host_cozuluyor_mu(None) is False


def test_nokta_ve_buyuk_harf_temizlenir(monkeypatch):
    gorulen = {}

    def yakala(h, *a, **k):
        gorulen["host"] = h
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(ssrf_guard.socket, "getaddrinfo", yakala)
    ssrf_guard.host_cozuluyor_mu("  Example.COM.  ")
    assert gorulen["host"] == "example.com"


def test_guvenlik_dali_DEGISMEDI(monkeypatch):
    """🪤 assert_public_host çözülemeyen host'ta hâlâ SESSİZCE geçmeli.
    Oraya istisna eklemek SSRF davranışını değiştirirdi; düzeltme ayrı
    fonksiyonla yapıldı."""
    def patla(*a, **k):
        raise socket.gaierror("nope")
    monkeypatch.setattr(ssrf_guard.socket, "getaddrinfo", patla)
    assert ssrf_guard.assert_public_host("bu-alan-yok-9x7z.invalid") is None


def test_tarama_girisi_kontrolu_KREDIDEN_ONCE():
    """🪤 Kontrol, kredi düşümü ve ücretsiz-hak sayacından ÖNCE olmalı.
    Sonra olsaydı yanlış yazılan alan yine kredi yakardı.
    main.py IMPORT EDİLMEZ — CI'ın asgari ortamında fastapi yok."""
    from pathlib import Path
    kaynak = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
    i_kontrol = kaynak.find("host_cozuluyor_mu, request.domain")
    assert i_kontrol > 0, "DNS varlık kontrolü tarama girişinde yok"

    # İmport satırları sırayı belirlemez — ÇAĞRI/RAISE noktalarına bakılır.
    # (Ölçüldü: DNS kontrolü 1002, kredi kontrolü 1018, ücretsiz kapı 1030.)
    for cagri in ('detail="insufficient_credits"', "free_scan_gate("):
        i = kaynak.find(cagri, i_kontrol)
        assert i > i_kontrol, f"{cagri} DNS kontrolünden ÖNCE çalışıyor"
