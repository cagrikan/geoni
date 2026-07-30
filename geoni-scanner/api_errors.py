"""Kullaniciya gorunen API hatalari — TEK KAYNAK, dile gore.

SORUN (2026-07-30 denetimi #9): main.py'deki HTTPException detail'leri sabit
metindi. Cogu Turkce, bir kismi Ingilizce; hangi dilde arayuz kullanilirsa
kullanilsin AYNI metin donuyordu. Ingilizce arayuzdeki musteri odeme adiminda
"Yetersiz token bakiyesi" goruyordu (ve TR arayuzdeki "Authentication required").

COZUM: hata KODLA firlatilir (`ApiHata`), metin yanit uretilirken istegin
diline gore secilir. Boylece:
  - endpoint'in dili bilmesi gerekmez (kod okunakli kalir),
  - ayni hata her yerde AYNI metni verir,
  - yeni dil eklemek tek sozluk degisikligi.

`detail` bilerek DUZ METIN doner. Web istemcisi `data.detail` degerini dogrudan
basiyor; sozluk donsek "[object Object]" gorunurdu. Kararli kod, ayri bir
`X-Error-Code` basligiyla verilir (istemci isterse metinden bagimsiz dallanir).

KAPSAM: yalnizca MUSTERIYE gorunen uclar. Admin paneli, webhook'lar ve ic
uclar (attest) kapsam disi — oralarda metin operatore/loga gider, ceviri
maliyeti karsiligini vermez. Yeni bir musteri-yuzu hata eklenirken buraya da
eklenir.
"""

VARSAYILAN_DIL = "tr"
DESTEKLENEN_DILLER = ("tr", "en")

MESAJLAR: dict[str, dict[str, str]] = {
    # ── Tarama baslatma ────────────────────────────────────────────────
    "gecersiz_alan_adi": {
        "tr": "Geçersiz web sitesi adresi. Örnek: example.com",
        "en": "Invalid website address. Example: example.com",
    },
    "gecersiz_hedef": {
        "tr": "Geçersiz hedef: yalnızca herkese açık siteler taranabilir.",
        "en": "Invalid target: only publicly reachable sites can be scanned.",
    },
    "ozel_tarama_giris_gerekli": {
        "tr": "Özel tarama için giriş gerekli.",
        "en": "Sign in to run a private scan.",
    },
    "tarama_baslatilamadi": {
        "tr": "Tarama şu anda başlatılamadı, lütfen birazdan tekrar deneyin.",
        "en": "The scan could not be started right now, please try again shortly.",
    },
    # ── Tarama sonucu ──────────────────────────────────────────────────
    "tarama_bulunamadi": {
        "tr": "Tarama bulunamadı.",
        "en": "Scan not found.",
    },
    "tarama_basarisiz": {
        # Ic hata metni BILEREK gosterilmiyor: eskiden `f"Audit failed: {error}"`
        # ile ham istisna metni istemciye gidiyordu (bilgi sizmasi). Ayrinti loga.
        "tr": "Tarama tamamlanamadı. Lütfen tekrar deneyin.",
        "en": "The scan could not be completed. Please try again.",
    },
    "sonuc_bulunamadi": {
        "tr": "Sonuç bulunamadı.",
        "en": "Result not found.",
    },
    # ── Hesap ──────────────────────────────────────────────────────────
    "giris_gerekli": {
        "tr": "Giriş yapmanız gerekiyor.",
        "en": "You need to sign in.",
    },
    "hesap_askida": {
        "tr": "Hesabınız askıya alınmış. Lütfen destek ile iletişime geçin.",
        "en": "Your account is suspended. Please contact support.",
    },
    "profil_kaydedilemedi": {
        "tr": "Profil kaydedilemedi, lütfen tekrar deneyin.",
        "en": "Your profile could not be saved, please try again.",
    },
    "hesap_silinemedi": {
        "tr": "Hesap silinemedi, lütfen tekrar deneyin.",
        "en": "Your account could not be deleted, please try again.",
    },
    "referans_kodu_olusturulamadi": {
        "tr": "Referans kodu oluşturulamadı, lütfen tekrar deneyin.",
        "en": "Your referral code could not be created, please try again.",
    },
    # ── Odeme / token ──────────────────────────────────────────────────
    "gecersiz_paket": {
        "tr": "Geçersiz paket.",
        "en": "Invalid package.",
    },
    "odeme_sayfasi_olusturulamadi": {
        "tr": "Ödeme sayfası oluşturulamadı. Lütfen birazdan tekrar deneyin.",
        "en": "The payment page could not be created. Please try again shortly.",
    },
    "yetersiz_bakiye": {
        "tr": "Yetersiz token bakiyesi.",
        "en": "Not enough token balance.",
    },
    "kullanici_bulunamadi": {
        "tr": "Kullanıcı bulunamadı.",
        "en": "User not found.",
    },
    # ── Hizmetler / biletler ───────────────────────────────────────────
    "gecersiz_bilet_turu": {
        "tr": "Geçersiz hizmet türü.",
        "en": "Invalid service type.",
    },
    "bilet_satin_alinamadi": {
        "tr": "Hizmet satın alınamadı.",
        "en": "The service could not be purchased.",
    },
    "hizmet_web_sitesi_gerektirir": {
        "tr": "Bu hizmet bir web sitesine uygulanır — lütfen geçerli bir web "
              "adresi (alan adı, ör. ornekmarka.com) girin. Kişi/marka/sosyal "
              "hedefleri için farklı hizmetler mevcut.",
        "en": "This service applies to a website — please enter a valid domain "
              "(e.g. examplebrand.com). Different services are available for "
              "person, brand and social targets.",
    },
    "hizmet_on_kosul_eksik": {
        "tr": "Bu hizmet için önce iki temel hizmeti almalısınız: AI Botlarına "
              "Erişim İzni ve Sitenizin AI Tarafından Doğru Anlaşılması. Onlar "
              "olmadan bu adım sonuç vermez.",
        "en": "This service needs the two foundation services first: AI Bot "
              "Access and Correct Understanding of Your Site by AI. Without "
              "them this step produces no result.",
    },
    "bilete_erisim_yok": {
        "tr": "Bu hizmet talebine erişiminiz yok.",
        "en": "You do not have access to this service request.",
    },
    "bilet_bulunamadi": {
        "tr": "Hizmet talebi bulunamadı.",
        "en": "Service request not found.",
    },
    "mesaj_gonderilemedi": {
        "tr": "Mesaj gönderilemedi.",
        "en": "The message could not be sent.",
    },
    "yukleme_linki_olusturulamadi": {
        "tr": "Yükleme linki oluşturulamadı.",
        "en": "The upload link could not be created.",
    },
}

# db.purchase_ticket'in dondurdugu ic hata kodu -> musteri mesaji kodu.
# ACIKLANABILIR olanlar burada; `exception`/`ticket_create_failed`/`not_configured`
# gibi SUNUCU tarafli arizalar bilerek disarida — musteriye ic ariza adi degil
# genel "satin alinamadi" mesaji doner (bilgi sizmasi + anlamsiz metin).
BILET_HATA_KODLARI = {
    "invalid_ticket_type": "gecersiz_bilet_turu",
    "invalid_target_domain": "hizmet_web_sitesi_gerektirir",
    "insufficient_balance": "yetersiz_bakiye",
    "user_not_found": "kullanici_bulunamadi",
    "prereq_missing": "hizmet_on_kosul_eksik",
}

# Musteriye acikca soylenmeyen (sunucu tarafli) bilet hata kodlari — testler
# bunlarin BILET_HATA_KODLARI'na eklenmeyi UNUTULMUS olmadigini dogrular.
BILET_SUNUCU_HATALARI = {"exception", "ticket_create_failed", "not_configured"}


def coz_dil(accept_language: str = "", x_lang: str = "") -> str:
    """Istekten dili cozer.

    Oncelik `X-Lang`: kullanicinin UYGULAMADAKI dil tercihi tarayici/cihaz
    dilinden farkli olabilir (TR tarayicida EN arayuz kullanan musteri var).
    Yoksa `Accept-Language`'in ilk etiketine, o da yoksa Turkce'ye duser.
    """
    if x_lang:
        etiket = x_lang.strip().lower()[:2]
        if etiket in DESTEKLENEN_DILLER:
            return etiket
    if accept_language:
        # "en-US,en;q=0.9,tr;q=0.8" -> "en"
        etiket = accept_language.split(",")[0].strip().lower()[:2]
        if etiket in DESTEKLENEN_DILLER:
            return etiket
    return VARSAYILAN_DIL


def mesaj(kod: str, dil: str = VARSAYILAN_DIL, **bicim) -> str:
    """Kodun secilen dildeki metni. Bilinmeyen kod, kodun KENDISINI doner —
    sessizce bos metin donmektense hata ayiklanabilir kalsin."""
    girdi = MESAJLAR.get(kod)
    if girdi is None:
        return kod
    metin = girdi.get(dil) or girdi[VARSAYILAN_DIL]
    return metin.format(**bicim) if bicim else metin


class ApiHata(Exception):
    """Kod tasiyan API hatasi; metin yanit uretilirken dile gore secilir.

    main.py'de `@app.exception_handler(ApiHata)` ile JSONResponse'a cevrilir.
    """

    def __init__(self, status_code: int, kod: str, **bicim):
        super().__init__(kod)
        self.status_code = status_code
        self.kod = kod
        self.bicim = bicim

    def metin(self, dil: str) -> str:
        return mesaj(self.kod, dil, **self.bicim)
