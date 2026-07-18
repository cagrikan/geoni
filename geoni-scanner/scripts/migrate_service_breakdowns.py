#!/usr/bin/env python3
"""
Fable'ın GELİŞTİRİLMİŞ iş kırılımlarını ticket_type_tasks'a uygula (2.2 revizyonu).

Neden: DB'deki görev setleri ESKİ. Fable (HIZMETLER-3-DERINLESME dokümanı) iş
kırılımlarını geliştirdi ve bazı paket KAPSAMLARINI değiştirdi:
  - wikidata_entity: 5 → 6 görev (YENİ "sameAs köprüsü" adımı — entity zinciri).
  - content_package: KAPSAM DEĞİŞTİ. Eski "2 içerik yaz" → yeni "3 brief + 1 tam
    taslak + yayın planı" (fulfill_content_ticket otomasyonu da bunu üretiyor;
    eski desc/delivery_template müşteriye YANLIŞ vaat veriyordu).
  - citation_placement: 5 → 6 görev (takip+kanıt ve teslim raporu ayrı adımlar).

how_to gövdeleri seed_how_to.HOW_TO'dan (Fable'ın 6/5/6 kırılımıyla zaten hizalı);
burada DOĞRU BAŞLIKLARLA eşlenir. Ortak teslim standardı (COMMON) her how_to'nun başına.

Güvenlik:
  - DRY-RUN varsayılan (DB'ye dokunmaz); --apply ile yazar.
  - Görevler: ticket_type_tasks ŞABLON satırları REPLACE edilir (eski sil + yeni ekle).
    Mevcut açık biletlerin ticket_tasks'ı DEĞERLE klonlandığından (FK yok) ETKİLENMEZ;
    yalnız YENİ satın almalar yeni kırılımı alır.
  - content_package desc/desc_en/delivery_template SADECE --apply-copy ile güncellenir
    (müşteriye görünen metin + fiyatlamaya komşu → ayrı bayrak, bilinçli onay).

Kullanım:
    ... python scripts/migrate_service_breakdowns.py              # dry-run (tam diff)
    ... python scripts/migrate_service_breakdowns.py --apply      # görev setlerini yaz
    ... python scripts/migrate_service_breakdowns.py --apply --apply-copy  # + content metni
"""
import os
import sys

import httpx

from seed_how_to import COMMON, HOW_TO

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

# Fable'ın geliştirilmiş kırılımına göre başlıklar (sıra = sort_order 1..N).
# HOW_TO[key] gövdeleriyle POZİSYONEL eşleşir (aynı uzunlukta olmalı).
TITLES = {
    "wikidata_entity": [
        "Mevcut varlık kontrolü (Wikidata tr/en + Google KG)",
        "Notability değerlendirmesi + bağımsız kaynak toplama",
        "Kaydı oluştur/zenginleştir (label + description + alias)",
        "Property'leri referanslarıyla ekle (S854 + S813)",
        "sameAs köprüsü (güncellenmiş schema.org bloğu)",
        "Doğrula + teslim raporu",
    ],
    "content_package": [
        "Konu seçimi (tarama verisinden)",
        "3 içerik brief'i hazırla",
        "İlk brief'in tam taslağını yaz",
        "Yayın planı",
        "Teslim + ölçüm vaadi",
    ],
    "citation_placement": [
        "Hedef-kaynak listesi (sov.sources + rakipler)",
        "Aksiyon tipi + öncelik sıralaması",
        "Outreach materyali hazırla",
        "Başvuru/outreach yürütme + log",
        "Yerleşimleri takip + kanıtla",
        "Teslim raporu",
    ],
}

# content_package müşteriye görünen metin — YENİ kapsam (3 brief + 1 taslak + plan).
CONTENT_COPY = {
    "description": (
        "AI motorlarının alıntılayacağı içerik paketi: taramanızdan seçilen "
        "gerçek hedef konularda 3 içerik brief'i + 1 tam yazı taslağı + yayın planı "
        "hazırlarız. Yayını siz yaparsınız; yayınlanan içeriği llms.txt'inize "
        "ücretsiz ekleriz."
    ),
    "description_en": (
        "Content built to be cited by AI engines: from your scan's real target "
        "topics we prepare 3 content briefs + 1 full article draft + a publishing "
        "plan. You publish; we add the published page to your llms.txt for free."
    ),
    "delivery_template": (
        "## Teslim: AI'ların Alıntılayacağı İçerik Paketi\n\n"
        "**Hedef:** {target}\n\n"
        "Aşağıda 3 içerik brief'i + ilk brief'in tam taslağı + yayın planı yer alır. "
        "Taslaktaki [köşeli parantez] alanlarına kendi verilerinizi (fiyat, vaka) "
        "ekleyin — AI motorları özgün veri veren kaynağı alıntılar.\n\n"
        "{content_briefs}\n\n---\n\n{content_draft}\n\n"
        "**Yayınlayınca:** içerik URL'sini bu bilete yazın; sayfayı llms.txt'inize "
        "ücretsiz ekleriz. 4-8 hafta sonra tekrar tarama ile kategori görünürlüğü "
        "artışını birlikte görelim."
    ),
}


def _h():
    return {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json"}


def main():
    apply = "--apply" in sys.argv
    apply_copy = "--apply-copy" in sys.argv
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print("HATA: SUPABASE_URL ve SUPABASE_SERVICE_KEY env gerekli."); sys.exit(1)
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"== Servis iş kırılımı migration — {mode}"
          f"{' (+content copy)' if apply_copy else ''} ==\n")

    with httpx.Client() as c:
        for key, titles in TITLES.items():
            bodies = HOW_TO[key]
            assert len(titles) == len(bodies), f"{key}: başlık/how_to uzunluk uyuşmazlığı"
            tt = c.get(f"{SUPABASE_URL}/rest/v1/ticket_types",
                       params={"key": f"eq.{key}", "select": "*"}, headers=_h(), timeout=15).json()
            if not isinstance(tt, list) or not tt:
                print(f"[{key}] ATLANDI: ticket_type yok.\n"); continue
            tid = tt[0]["id"]
            cur = c.get(f"{SUPABASE_URL}/rest/v1/ticket_type_tasks",
                        params={"ticket_type_id": f"eq.{tid}", "select": "id,sort_order,title",
                                "order": "sort_order.asc"}, headers=_h(), timeout=15).json()
            print(f"[{key}] (id={tid})  MEVCUT {len(cur)} görev → YENİ {len(titles)} görev")
            for t in cur:
                print(f"    - eski #{t['sort_order']} {t['title']}")
            for i, ttl in enumerate(titles, 1):
                print(f"    + yeni #{i} {ttl}")
            if apply:
                # REPLACE: eski şablon görevleri sil, yeni seti ekle.
                d = c.delete(f"{SUPABASE_URL}/rest/v1/ticket_type_tasks",
                             params={"ticket_type_id": f"eq.{tid}"}, headers=_h(), timeout=15)
                rows = [{"ticket_type_id": tid, "sort_order": i + 1, "title": ttl,
                         "how_to": COMMON + bodies[i]} for i, ttl in enumerate(titles)]
                ins = c.post(f"{SUPABASE_URL}/rest/v1/ticket_type_tasks", headers=_h(),
                             json=rows, timeout=20)
                ok = d.status_code in (200, 204) and ins.status_code in (200, 201, 204)
                print(f"    → {'YAZILDI' if ok else f'HATA del={d.status_code} ins={ins.status_code}: {ins.text[:120]}'}")
            print()

        # content_package müşteri metni (ayrı bayrak).
        print("[content_package] MÜŞTERİYE GÖRÜNEN METİN (eski → yeni):")
        cur = c.get(f"{SUPABASE_URL}/rest/v1/ticket_types",
                    params={"key": "eq.content_package", "select": "id,description,delivery_template"},
                    headers=_h(), timeout=15).json()
        if isinstance(cur, list) and cur:
            print(f"    eski desc: {(cur[0].get('description') or '')[:110]!r}")
            print(f"    yeni desc: {CONTENT_COPY['description'][:110]!r}")
            print(f"    eski template: {(cur[0].get('delivery_template') or '')[:90]!r}")
            print(f"    yeni template: {CONTENT_COPY['delivery_template'][:90]!r}")
            if apply and apply_copy:
                pr = c.patch(f"{SUPABASE_URL}/rest/v1/ticket_types",
                             params={"id": f"eq.{cur[0]['id']}"}, headers=_h(),
                             json=CONTENT_COPY, timeout=15)
                print(f"    → {'YAZILDI' if pr.status_code in (200,204) else f'HATA {pr.status_code}: {pr.text[:120]}'}")
            elif apply:
                print("    → ATLANDI (--apply-copy verilmedi; müşteri metni fiyatlamaya komşu, bilinçli onay).")
        print()
    if not apply:
        print("Dry-run bitti. Görev setleri: --apply ; content müşteri metni de: --apply --apply-copy")


if __name__ == "__main__":
    main()
