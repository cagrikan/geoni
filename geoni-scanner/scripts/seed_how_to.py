#!/usr/bin/env python3
"""
2.2 — how_to metinlerini ticket_type_tasks'a yaz (denetim A-2 kapanışı).

how_to müşteriye GÖRÜNMEZ (main.py ticket_tasks_ep bunu müşteri için siler);
uzmana yol gösteren rehberdir. Otomasyon düşüp bilet uzmana geçtiğinde uzmanın
elinde tam rehber olsun diye ticket_type_tasks (ŞABLON) satırlarına yazılır.

Güvenlik/dürüstlük:
  - DRY-RUN varsayılan: neyin yazılacağını gösterir, DB'ye DOKUNMAZ. Yazmak için --apply.
  - Yalnız `how_to` alanını PATCH eder (title/sort_order'a dokunmaz). İdempotent.
  - Eşleme sort_order'a göre POZİSYONEL; beklenen görev sayısı tutmuyorsa o hizmeti
    ATLAR (yanlış göreve metin yazmaktansa dokunmamak). Uyuşmazlığı raporlar.
  - Prod Supabase gerekir: SUPABASE_URL + SUPABASE_SERVICE_KEY env'de olmalı
    (App Runner'da secret; lokal çalıştırmak için Secrets Manager'dan alınır).

Kullanım:
    SUPABASE_URL=... SUPABASE_SERVICE_KEY=... python scripts/seed_how_to.py          # dry-run
    SUPABASE_URL=... SUPABASE_SERVICE_KEY=... python scripts/seed_how_to.py --apply   # yaz
"""
import os
import sys

import httpx

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

# Tüm hizmetlerin how_to'larının başına eklenecek ortak teslim standardı (A-2).
COMMON = (
    "**Ortak teslim standardı:** Teslim dili müşterinin tarama diliyle aynıdır "
    "(TR tarama → TR teslim). Şablondaki tüm {...} alanlarını doldurmadan gönderme. "
    "Her iddia kanıtlı: URL + tarih. Uzmanlık dışı büyük vaat verme; ölçülebilir "
    "vaat ver (\"bir sonraki taramada X metriği\"). Dosya teslimlerinde markdown "
    "kullan; müşteri adı ve hedefi her dosyanın başında yazsın.\n\n"
)

# Her hizmetin görevleri SIRAYLA (sort_order 1..N ile eşleşmeli).
HOW_TO = {
    "wikidata_entity": [
        "Mevcut varlık kontrolü: Wikidata'da wikidata.org/w/index.php?search= ile "
        "marka/kişi adını TR ve EN ara; ad varyantlarını (kısaltma, domain adı, ticari "
        "unvan) dene. Google için kgsearch API'sini veya bilgi paneli olup olmadığını "
        "kontrol et. Kayıt VARSA yeni açma — mevcut kaydı zenginleştir (görev 4'e geç) "
        "ve rapora \"mevcut kayıt geliştirildi\" yaz.",

        "Notability + kaynak toplama: En az 2 bağımsız, kamuya açık, ciddi kaynak bul "
        "(haber, ticaret sicili/MERSİS, oda-dernek kaydı, akademik atıf). Markanın kendi "
        "sitesi ve sosyal profilleri notability kaynağı SAYILMAZ. Taramanın sov.sources "
        "listesi iyi başlangıçtır. Yeterli kaynak YOKSA kayıt açma; müşteriye dürüst yaz "
        "ve citation hizmetini öner. Silinen kayıt açmaktan kötüdür.",

        "Kayıt oluşturma (label/description/alias): Label tr+en resmi ad, hukuki ek "
        "(A.Ş., Ltd.) label'a değil alias'a. Description tr+en 2-5 kelime, nötr (\"Türk "
        "yazılım şirketi\"); \"lider/en iyi\" reklam dili anında geri alınır. Alias: "
        "kısaltmalar, eski ad, domain adı.",

        "Property + referans: Asgari P31 (business/human/brand), P856 resmi site, "
        "P17/P159 (kişide P27, P106). site_assets.sameAs'taki sosyal profilleri ilgili "
        "property'lere ekle (P2002 X, P2003 Instagram, P2013 Facebook, P2397 YouTube, "
        "P4264/P6634 LinkedIn). HER iddiaya referans: reference URL (S854) + retrieved "
        "(S813, bugün). Kişisel verileri yalnız müşteri onayı + kamu kaynağıyla ekle.",

        "sameAs köprüsü: Müşterinin schema.org JSON-LD'sine (schema_setup teslimi) "
        "\"sameAs\" dizisi olarak Wikidata QID URL'sini + sosyal profilleri ekleyen blok "
        "üret ve bilete dosya ekle; \"mevcut şema bloğunuzla değiştirin\" talimatı ver. "
        "Sosyal profillerin web sitesi alanında resmi sitenin yazılı olduğunu kontrol et.",

        "Doğrulama + rapor: 24-72 saat sonra Wikidata aramasında görünürlüğü doğrula. "
        "Rapor: QID linki, eklenen property tablosu, kullanılan kaynaklar, KG durumu "
        "(\"panel yok — 2-6 hafta içinde kontrol edilecek\"), skor etkisi (\"bir sonraki "
        "taramada otorite +8 Wikidata bonusu\"). Kanıt: QID URL'si.",
    ],
    "content_package": [
        "Konu seçimi (tarama verisinden): Öncelik: (1) sov.queries mentioned=False "
        "sorgular — AI'a soruldu, müşteri anılmadı; brief'in hedef sorgusu budur; "
        "(2) opportunities; (3) rakip anılan sorgular → karşılaştırma içeriği. "
        "top_topics'teki güçlü konulara YENİ içerik önerme — onlar derinleştirme adayı. "
        "Her konu için gerekçeni brief'e yaz.",

        "Brief hazırlama: Her brief: başlık (soru formunda), hedef sorgu(lar), "
        "ilk-paragraf-cevabı talimatı (net cevap İLK 2 cümlede), H2 ana sorular, özgün "
        "değer öğesi (fiyat/vaka/veri — başka yerde olmayan bir şey şart), SSS listesi "
        "(FAQPage), uzunluk, dil, yayın yeri. Standart: 3 brief + 1 tam taslak.",

        "Taslak yazımı: Cevap-odaklı yaz: soru-başlık, ilk 2 cümlede net cevap, bağımsız "
        "alıntılanabilir H2 bölümleri, sonda SSS. E-E-A-T: yazar adı+unvan, tarih, dışa "
        "kaynakça. Müşterinin doldurması gereken yerleri [KÖŞELİ PARANTEZ] ile işaretle "
        "(fiyat, vaka). Jenerik doldurma yazma — kapsam > kelime sayısı.",

        "Yayın planı: Kanal sırası: kendi blog (ana — atıf kendi domain'e gelsin) → "
        "1 hafta sonra LinkedIn/Medium özet+link (birebir kopya değil) → uygunsa sektör "
        "sitesine misafir yazı (citation hizmetiyle köprü). Her içerik için yayın sonrası: "
        "llms.txt güncellemesi (ücretsiz vaadimiz), sitemap, sosyal paylaşım.",

        "Teslim + ölçüm: Dosyalar: brief'ler + taslak + yayın planı. Teslim mesajında "
        "ölçüm vaadi: \"yayınladıktan 4-8 hafta sonra tekrar tarama — hedef sorgularda "
        "anılma artışını birlikte görelim.\" Yayınlanan URL'leri bilete yazmasını iste. "
        "Sosyal hedefte: brief yerine bio/açıklama seti + platform uzun metinleri + "
        "3 konu × platform içerik dizisi.",
    ],
    "citation_placement": [
        "Hedef-kaynak listesi: sov.sources'ı aç: own=False + mentions yüksek domain'ler "
        "birincil hedef — AI bu kategoride ORADAN besleniyor. sov.competitors'ın hangi "
        "sorgularda anıldığına bak; o yanıtların kaynak sitelerini ekle. citation_gap "
        "(rakip anan, sen yok) en öncelikli. 10+ kaynak, her birine tek cümle gerekçe.",

        "Aksiyon tipi + öncelik: Her kaynağı sınıfla: dizin (form — düşük çaba, ilk hafta), "
        "listicle (editöre ekleme talebi — en yüksek getiri/çaba, AI'ların en çok "
        "alıntıladığı format), sektör medyası (misafir yazı), röportaj/podcast (pitch). "
        "Kişi hedefinde: konuşmacı sayfaları, \"takip edilecek N kişi\" listeleri, "
        "dernek/etkinlik sayfaları. Kolay kazanımları öne al.",

        "Outreach materyali: 2-3 şablon: (a) listicle ekleme talebi — rakibin listede "
        "olduğunu söyle, müşterinin somut farkını 1 cümlede ver; (b) misafir yazı pitch'i "
        "(content_package brief'i varsa kullan); (c) uzman görüşü teklifi. Kişiselleştir. "
        "Toplu/jenerik e-posta gönderme — spam hem sonuçsuz hem itibar riski.",

        "Yürütme + log: Dizin başvurularını yap (NAP tutarlılığı: ad-adres-telefon her "
        "yerde AYNI). E-postaları gönder; her satıra tarih+durum işle. Satın alınmış link, "
        "spam yorum, sahte forum hesabı KESİNLİKLE yok — yakalanırsa kalıcı zarar. "
        "Forumlarda ancak gerçekten cevap olduğun yerde markayı an.",

        "Kanıt + takip: Her yerleşim: canlı URL + archive.org arşiv kopyası + hangi "
        "taktikle kazanıldı. Anılmanın bağlam içinde olduğunu kontrol et (marka adı + "
        "kategori kelimesi aynı cümlede; çıplak link zayıf). 2 hafta cevapsız kalanlara "
        "tek nazik hatırlatma.",

        "Teslim raporu: Tablo: kazanılan yerleşimler (URL+arşiv), bekleyenler, "
        "reddedilenler+nedeni. Ölçüm vaadi: \"4-8 hafta sonra tekrar taramada "
        "own_cited_count / kategori görünürlüğü artışını göreceğiz.\" Kanıt alanına en "
        "güçlü yerleşim URL'sini yaz.",
    ],
}


def _headers():
    return {"apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json"}


def main():
    apply = "--apply" in sys.argv
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print("HATA: SUPABASE_URL ve SUPABASE_SERVICE_KEY env gerekli.")
        sys.exit(1)
    mode = "APPLY (DB'ye yazılıyor)" if apply else "DRY-RUN (DB'ye dokunulmuyor; yazmak için --apply)"
    print(f"== how_to seed — {mode} ==\n")

    total_written = 0
    with httpx.Client() as c:
        for key, texts in HOW_TO.items():
            tt = c.get(f"{SUPABASE_URL}/rest/v1/ticket_types",
                       params={"key": f"eq.{key}", "select": "id"}, headers=_headers(), timeout=15)
            rows = tt.json() if tt.status_code == 200 else []
            if not rows:
                print(f"[{key}] ATLANDI: ticket_type bulunamadı.\n")
                continue
            tid = rows[0]["id"]
            tr = c.get(f"{SUPABASE_URL}/rest/v1/ticket_type_tasks",
                       params={"ticket_type_id": f"eq.{tid}", "select": "id,title,sort_order,how_to",
                               "order": "sort_order.asc"}, headers=_headers(), timeout=15)
            tasks = tr.json() if tr.status_code == 200 else []
            if len(tasks) != len(texts):
                print(f"[{key}] ATLANDI: DB'de {len(tasks)} görev var, {len(texts)} metin "
                      f"bekleniyor — pozisyonel eşleme güvensiz. Görev başlıkları:")
                for t in tasks:
                    print(f"    #{t['sort_order']} {t['title']!r}")
                print("  (Görev seti eşleşince tekrar çalıştır ya da metinleri hizala.)\n")
                continue
            print(f"[{key}] {len(tasks)} görev eşleşti:")
            for t, body in zip(tasks, texts):
                how_to = COMMON + body
                exists = "✓ zaten var" if t.get("how_to") else "→ yazılacak"
                print(f"    #{t['sort_order']} {t['title'][:48]!r:50} {exists}")
                if apply:
                    pr = c.patch(f"{SUPABASE_URL}/rest/v1/ticket_type_tasks",
                                 params={"id": f"eq.{t['id']}"}, headers=_headers(),
                                 json={"how_to": how_to}, timeout=15)
                    if pr.status_code not in (200, 204):
                        print(f"      HATA PATCH {pr.status_code}: {pr.text[:120]}")
                    else:
                        total_written += 1
            print()
    if apply:
        print(f"Tamam: {total_written} görev how_to güncellendi.")
    else:
        print("Dry-run bitti. Yazmak için: python scripts/seed_how_to.py --apply")


if __name__ == "__main__":
    main()
