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
