#!/usr/bin/env python3
"""
Pricing 1.0.1 — insan-teslimli hizmetlerin bedelini gerçek değere çıkar.

Karar (pricing-plan-1-0-1): insan/yarı-otonom hizmetler zarar fiyatına satılıyordu.
token_cost gerçek değere; money_price (doğrudan $ satın alma) token değerine hizalı.
Otomatik hizmetler (llms_robots/schema_setup) + taramalar DEĞİŞMEZ (funnel korunur).

⚠️ money_price yalnız DB display + doğrudan satın alma tutarıdır; APP STORE CONNECT'teki
IAP ürün fiyatı (ai.geoni.service.*) ile POLAR ürün fiyatı da ELLE hizalanmalı (Çağrı).
App şu an review'da/YAYINDA DEĞİL → canlı exploit yok; resubmit'ten önce store fiyatları
+ 2500/5000 token paketleri oluşturulmalı.

DRY-RUN default; --apply ile yazar.
"""
import os
import sys

import httpx

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

# key -> (token_cost, money_price). ~$ token değeri (rate ~$0.09/tok) clean tier'a.
NEW_PRICING = {
    "citation_placement": (1500, 134.99),   # 200 → 1500 (~$135)
    "content_package":    (2000, 179.99),    # 250 → 2000 (~$180)
    "wikidata_entity":    (1200, 107.99),    # 300 → 1200 (~$108)
    # DEĞİŞMEZ (funnel): llms_robots 100/$9.99, schema_setup 150/$14.99
}


def _h():
    return {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json"}


def main():
    apply = "--apply" in sys.argv
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print("HATA: SUPABASE_URL + SUPABASE_SERVICE_KEY gerekli."); sys.exit(1)
    print(f"== Pricing 1.0.1 — {'APPLY' if apply else 'DRY-RUN'} ==\n")
    with httpx.Client() as c:
        for key, (tok, money) in NEW_PRICING.items():
            r = c.get(f"{SUPABASE_URL}/rest/v1/ticket_types",
                      params={"key": f"eq.{key}", "select": "id,token_cost,money_price,apple_product_id,polar_product_id"},
                      headers=_h(), timeout=15).json()
            if not isinstance(r, list) or not r:
                print(f"[{key}] ATLANDI: yok\n"); continue
            row = r[0]
            print(f"[{key}] token {row.get('token_cost')} → {tok} | money ${row.get('money_price')} → ${money}")
            print(f"    apple_product_id={row.get('apple_product_id')}  polar={row.get('polar_product_id')}")
            print(f"    ⚠️ App Store Connect + Polar ürün fiyatı da ${money}'a çekilmeli (elle).")
            if apply:
                pr = c.patch(f"{SUPABASE_URL}/rest/v1/ticket_types",
                             params={"id": f"eq.{row['id']}"}, headers=_h(),
                             json={"token_cost": tok, "money_price": money}, timeout=15)
                print(f"    → {'YAZILDI' if pr.status_code in (200,204) else f'HATA {pr.status_code}: {pr.text[:120]}'}")
            print()
    if not apply:
        print("Dry-run bitti. Yazmak için --apply")
    else:
        print("DB güncellendi. SONRAKİ (Çağrı, store tarafı):\n"
              "  1) App Store Connect: 3 hizmet IAP ürününün fiyatını yukarıdaki $'a çek.\n"
              "  2) Polar: aynı 3 ürünün fiyatını güncelle.\n"
              "  3) YENİ 2500 (~$179) ve 5000 (~$299) token paketi (IAP + Polar) oluştur.\n"
              "  4) Build'i resubmit et.")


if __name__ == "__main__":
    main()
