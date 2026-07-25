"""App Attest dogrulama testleri.

Gercek attestation (Apple sertifika zinciri) sentezlenemez; ama HER ISTEKTE calisan
kritik yol assertion dogrulamasidir ve o tamamen test edilebilir: imza, uygulama
kimligi (rpIdHash) ve replay engeli (counter).
"""
import base64
import hashlib

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

import attest


def _auth_data(app_id: str, counter: int) -> bytes:
    """authenticatorData: rpIdHash(32) | flags(1) | counter(4)"""
    return hashlib.sha256(app_id.encode()).digest() + b"\x00" + counter.to_bytes(4, "big")


def _make_assertion(key, app_id: str, counter: int, client_data: str) -> str:
    ad = _auth_data(app_id, counter)
    nonce = hashlib.sha256(ad + hashlib.sha256(client_data.encode()).digest()).digest()
    sig = key.sign(nonce, ec.ECDSA(hashes.SHA256()))
    return base64.b64encode(cbor2.dumps({"signature": sig, "authenticatorData": ad})).decode()


@pytest.fixture
def anahtar():
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture
def pub_der(anahtar):
    return anahtar.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def test_gecerli_assertion_dogrulanir(anahtar, pub_der):
    a = _make_assertion(anahtar, attest.APP_ID, 5, "istek-1")
    assert attest.verify_assertion(a, "istek-1", pub_der, stored_counter=4) == 5


def test_replay_reddedilir(anahtar, pub_der):
    """Ayni assertion tekrar gonderilirse counter artmadigi icin reddedilmeli."""
    a = _make_assertion(anahtar, attest.APP_ID, 7, "istek-2")
    assert attest.verify_assertion(a, "istek-2", pub_der, stored_counter=6) == 7
    with pytest.raises(ValueError, match="counter"):
        attest.verify_assertion(a, "istek-2", pub_der, stored_counter=7)


def test_eski_counter_reddedilir(anahtar, pub_der):
    a = _make_assertion(anahtar, attest.APP_ID, 3, "istek-3")
    with pytest.raises(ValueError, match="counter"):
        attest.verify_assertion(a, "istek-3", pub_der, stored_counter=10)


def test_baska_uygulama_kimligi_reddedilir(anahtar, pub_der):
    """Baska bir bundle/team icin uretilmis assertion kabul edilmemeli."""
    a = _make_assertion(anahtar, "XXXXXXXXXX.com.baska.app", 2, "istek-4")
    with pytest.raises(ValueError, match="rpIdHash"):
        attest.verify_assertion(a, "istek-4", pub_der, stored_counter=1)


def test_baska_anahtarla_imza_reddedilir(anahtar):
    """Saldirgan kendi anahtariyla imzalarsa, kayitli public key ile dogrulanmamali."""
    baska = ec.generate_private_key(ec.SECP256R1())
    baska_pub = baska.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    a = _make_assertion(anahtar, attest.APP_ID, 4, "istek-5")
    with pytest.raises(Exception):
        attest.verify_assertion(a, "istek-5", baska_pub, stored_counter=3)


def test_client_data_degistirilirse_reddedilir(anahtar, pub_der):
    """Imza istek govdesine baglidir; govde degisirse imza tutmaz (MITM/replay)."""
    a = _make_assertion(anahtar, attest.APP_ID, 6, "istek-orijinal")
    with pytest.raises(Exception):
        attest.verify_assertion(a, "istek-DEGISTIRILMIS", pub_der, stored_counter=5)


def test_auth_data_ayristirma():
    ad = _auth_data(attest.APP_ID, 42)
    p = attest._parse_auth_data(ad)
    assert p["counter"] == 42
    assert p["rp_id_hash"] == hashlib.sha256(attest.APP_ID.encode()).digest()


def test_kisa_auth_data_hata_verir():
    with pytest.raises(ValueError):
        attest._parse_auth_data(b"kisa")


def test_enforce_varsayilan_kapali():
    """Faz 1: zorunlu kilma KAPALI olmali — attest'siz eski surumler kirilmasin."""
    assert attest.ENFORCE is False, "ATTEST_ENFORCE varsayilan olarak kapali kalmali"
