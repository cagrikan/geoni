"""RevenueCat alimlarinin DOGRU magazaya yazildigini dogrular.

2026-07-29: webhook kanali sabit "ios" yaziyordu (yalniz iOS varken yazilmisti).
Android yayina cikinca Play alimlari deftere "ios_sandbox" olarak gecti
(rc_B776BFBA...), admin ciro raporu Android gelirini iOS'a yazdi. Ayrica
sandbox alimlar gercek ciroya dahil ediliyordu.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import iap


def _olay(store, environment="PRODUCTION", etype="NON_RENEWING_PURCHASE"):
    return {"event": {
        "type": etype, "app_user_id": "u-1", "product_id": "ai.geoni.tokens.100",
        "id": "evt-1", "price_in_purchased_currency": 739.99, "currency": "TRY",
        "environment": environment, "store": store,
    }}


# --- parse_event magazayi tasiyor mu --------------------------------------

def test_parse_event_store_tasinir():
    assert iap.parse_event(_olay("PLAY_STORE"))["store"] == "PLAY_STORE"
    assert iap.parse_event(_olay("APP_STORE"))["store"] == "APP_STORE"


def test_store_yoksa_ios_varsayilmaz():
    """Asil hata buydu: bilinmeyen magaza sessizce iOS sayiliyordu."""
    olay = _olay("PLAY_STORE")
    del olay["event"]["store"]
    e = iap.parse_event(olay)
    assert e["store"] == "UNKNOWN"
    kanal, _ = iap.channel_and_label(e["store"], sandbox=False)
    assert kanal != "ios"


# --- kanal esleme ----------------------------------------------------------

def test_play_alimi_android_kanalina_yazilir():
    kanal, etiket = iap.channel_and_label("PLAY_STORE", sandbox=False)
    assert kanal == "android"
    assert etiket == "Play Store"


def test_app_store_alimi_ios_kanalina_yazilir():
    kanal, etiket = iap.channel_and_label("APP_STORE", sandbox=False)
    assert kanal == "ios"
    assert etiket == "App Store"


def test_mac_app_store_da_ios_sayilir():
    assert iap.channel_and_label("MAC_APP_STORE", sandbox=False)[0] == "ios"


def test_play_lisans_testi_ayri_kanala_yazilir():
    """Play lisans testi alimi: RevenueCat environment=SANDBOX dondurur.
    iOS sandbox ile ayni kovaya DUSMEMELI."""
    kanal, etiket = iap.channel_and_label("PLAY_STORE", sandbox=True)
    assert kanal == "android_sandbox"
    assert etiket == "Play Store"
    assert kanal != iap.channel_and_label("APP_STORE", sandbox=True)[0]


def test_bilinmeyen_magaza_kendi_kanalini_alir():
    assert iap.channel_and_label("AMAZON", sandbox=False)[0] == "amazon"
    assert iap.channel_and_label("YENI_MAGAZA", sandbox=False)[0] == "yeni_magaza"


# --- ciro raporu sandbox'i dislar -----------------------------------------

def test_sandbox_kanallari_isaretlenir():
    assert iap.is_sandbox_channel("android_sandbox")
    assert iap.is_sandbox_channel("ios_sandbox")
    assert not iap.is_sandbox_channel("android")
    assert not iap.is_sandbox_channel("ios")
    assert not iap.is_sandbox_channel("web")
    assert not iap.is_sandbox_channel(None)


def test_uretim_kanallari_asla_sandbox_sayilmaz():
    """channel_and_label'in urettigi her uretim kanali ciroya girmeli."""
    for store in ["APP_STORE", "MAC_APP_STORE", "PLAY_STORE", "AMAZON", "STRIPE"]:
        kanal, _ = iap.channel_and_label(store, sandbox=False)
        assert not iap.is_sandbox_channel(kanal), store
        s_kanal, _ = iap.channel_and_label(store, sandbox=True)
        assert iap.is_sandbox_channel(s_kanal), store
