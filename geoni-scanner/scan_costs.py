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

WEB_SCAN_COST = 5
BRAND_SCAN_COST = 10          # kişi + marka aynı ücret
SOCIAL_SCAN_COST = 0          # sosyal tarama ücretsiz (main.py: deduct=not social)

# Public uç bunu olduğu gibi döndürür; istemci "≈N tarama" hesabını buradan yapar.
SCAN_COSTS = {
    "web": WEB_SCAN_COST,
    "person": BRAND_SCAN_COST,
    "brand": BRAND_SCAN_COST,
    "social": SOCIAL_SCAN_COST,
}
