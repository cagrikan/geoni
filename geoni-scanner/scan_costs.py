"""Tarama kontör maliyetleri — TEK KAYNAK.

Bu sayılar 2026-07-31'e kadar altı ayrı yerde elle yazılıydı (`main.py`'de iki
sabit + iki düz sayı, `db.py`'de üç düz sayı). Biri değişip diğeri kalırsa
`audits.credits_spent` ile gerçekten düşülen tutar birbirini tutmaz — tam da
2026-07-28'de anonim taramalarda yaşanan hatanın (218 satır, 1120 hayalî kredi)
biçimi. Ayrıca arayüz "20 token ≈ kaç tarama" diyebilmek için bu sayılara
ihtiyaç duyuyor; oraya elle yazmak yedinci kopya olurdu.

Bağımlılığı yok (fastapi/pydantic import etmez): deploy kapısı testleri
minimal ortamda koşuyor, ağır bağımlılık modül seviyesinde import edilemez.
Bkz. [[feedback-ci-ortaminda-dogrula]].
"""

# 2026-08-03 (kurucu kararı): yeni kullanıcı 20 token alıyor (ölçüldü: son 17
# kayıt) ve kural "bir kişi bir kere ücretsiz tarar". Bunu ayrı bir sayaçla
# değil TOKENLE uygulamak için tam tarama bedeli = açılış hediyesi yapıldı:
#   20 token / 20 token = tam 1 tarama.
# Sosyal yarı fiyat: viral kanca (geoni.ai/s/<jobId> paylaşımı) huninin ilk
# teması, tamamen kapatmak o döngüyü keserdi; bedava bırakmak ise 20 token
# kuralının tek deliği olurdu (login sonrası sınırsız bedava tarama).
#
# ⚠️ Ölçülen gerçek maliyet $0.3194/tarama (2026-08-03, canlı API usage
# alanlarından). Eski 5 token'lık web taraması 100'lük pakette $0.50'ye
# satılıyordu — yani neredeyse maliyetine. Yeni bedel marjı düzeltir.
WEB_SCAN_COST = 20
BRAND_SCAN_COST = 20          # kişi + marka aynı ücret
SOCIAL_SCAN_COST = 10         # yarı fiyat — viral kanca korunur ama bedava değil

# Public uç bunu olduğu gibi döndürür; istemci "≈N tarama" hesabını buradan yapar.
SCAN_COSTS = {
    "web": WEB_SCAN_COST,
    "person": BRAND_SCAN_COST,
    "brand": BRAND_SCAN_COST,
    "social": SOCIAL_SCAN_COST,
}


def bedel_sec(entity_type: str | None, social: bool = False) -> int:
    """Kişi/marka/sosyal taramanın bedelini TEK noktadan seçer.

    Neden (fonksiyonel denetim 2026-08-12, Ö2): private akışta ön-kontrol
    bedeli `social` bayrağına göre seçiyordu ama fiili düşüm SABİT
    BRAND_SCAN_COST yazıyordu — `{social:true, private:true}` gönderen istemci
    ya 10-19 bakiyeyle kapıyı geçip düşümde takılıyor (tarama koşmuş, GEONI
    maliyeti oluşmuş) ya da yarı fiyatlı hizmete 20 ödüyordu. İki nokta da
    artık buradan okur; sayı değişirse ikisi birlikte değişir.

    🪤 `type` alanı istemciden serbest metin gelir; `social` bayrağı da ayrı
    taşınır. İkisinden HERHANGİ biri sosyalse sosyal tarife uygulanır;
    bilinmeyen tip güvenli tarafa (tam bedel, BRAND) düşer — asla 0 değil."""
    if social or (entity_type or "") == "social":
        return SOCIAL_SCAN_COST
    return SCAN_COSTS.get(entity_type or "person", BRAND_SCAN_COST)
