#!/usr/bin/env python3
"""
2500 + 5000 token paketlerini credit_packages'a ekle (pricing 1.0.1 boşluğu).

Neden: yeni insan/yarı-otonom hizmetler 1200-2000 token; en büyük paket 1000
token ($79.99) → tek paketle alınamıyor. 2500 (~$179) ve 5000 (~$299) eklenir.

apple_product_id konvansiyonu: ai.geoni.tokens.{credits}. polar_product_id Polar'da
ürün oluşturulunca ELLE doldurulur (UUID Polar üretir; şimdilik null).
⚠️ Satın alınabilir olması için Çağrı: (1) App Store Connect'te ai.geoni.tokens.2500
ve .5000 IAP ürünlerini $179.99/$299.99 ile oluşturmalı; (2) Polar'da aynı ürünleri
oluşturup polar_product_id'lerini bu satırlara yazmalı. App yayında değil → risk yok.

İdempotent: aynı credits varsa TEKRAR EKLEMEZ (günceller). DRY-RUN default; --apply ile yazar.
"""
import os
import sys

import httpx

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

NEW_PACKAGES = [
    {"name": "Geoni.AI - 2500 Token", "credits": 2500, "display_price": 179.99,
     "currency": "USD", "is_active": True, "apple_product_id": "ai.geoni.tokens.2500",
     "polar_product_id": None},
    {"name": "Geoni.AI - 5000 Token", "credits": 5000, "display_price": 299.99,
     "currency": "USD", "is_active": True, "apple_product_id": "ai.geoni.tokens.5000",
     "polar_product_id": None},
]


def _h():
    return {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json"}


def main():
    apply = "--apply" in sys.argv
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print("HATA: SUPABASE_URL + SUPABASE_SERVICE_KEY gerekli."); sys.exit(1)
    print(f"== Token paketleri ekle — {'APPLY' if apply else 'DRY-RUN'} ==\n")
    with httpx.Client() as c:
        existing = c.get(f"{SUPABASE_URL}/rest/v1/credit_packages",
                         params={"select": "id,credits,display_price"}, headers=_h(), timeout=15).json()
        by_credits = {p["credits"]: p for p in existing} if isinstance(existing, list) else {}
        for pkg in NEW_PACKAGES:
            cur = by_credits.get(pkg["credits"])
            if cur:
                print(f"[{pkg['credits']} tok] ZATEN VAR (${cur.get('display_price')}) → ${pkg['display_price']} güncellenecek")
                if apply:
                    r = c.patch(f"{SUPABASE_URL}/rest/v1/credit_packages",
                                params={"id": f"eq.{cur['id']}"}, headers=_h(),
                                json={k: pkg[k] for k in ("name", "display_price", "currency", "is_active", "apple_product_id")},
                                timeout=15)
                    print(f"    → {'GÜNCELLENDİ' if r.status_code in (200,204) else f'HATA {r.status_code}: {r.text[:120]}'}")
            else:
                print(f"[{pkg['credits']} tok] YENİ → ${pkg['display_price']}  apple={pkg['apple_product_id']}  polar=(elle)")
                if apply:
                    r = c.post(f"{SUPABASE_URL}/rest/v1/credit_packages", headers=_h(), json=pkg, timeout=15)
                    print(f"    → {'EKLENDİ' if r.status_code in (200,201,204) else f'HATA {r.status_code}: {r.text[:140]}'}")
            print()
    if not apply:
        print("Dry-run bitti. Yazmak için --apply")
    else:
        print("DB'ye eklendi. SONRAKİ (Çağrı): App Store Connect'te ai.geoni.tokens.2500/.5000 IAP\n"
              "ürünleri ($179.99/$299.99) + Polar ürünleri oluştur, polar_product_id'leri satırlara yaz.")


if __name__ == "__main__":
    main()
