# Knowledge Graph yapılandırması

6 Eylül 2026: Gemini authorization anahtarı model listesini okuyabiliyor, fakat
KG Search aynı anahtarı reddediyor. KG için `GOOGLE_KG_API_KEY` ayrıldı.
`GOOGLE_API_KEY` Gemini çağrılarında kalır; KG için geri dönüş anahtarı değildir.

Üretim öncesi uygulanacak kapsam:

Ön koşul: Google yeni kullanıcıları Cloud Enterprise Knowledge Graph'a
yönlendiriyor. Mevcut projenin legacy KG erişimi ve kota/ücret koşulları
doğrulanmadan API etkinleştirilmemeli; Enterprise ürününe otomatik geçilmez.
Bu yerel yama mevcut legacy uç noktasını korur, ücretsiz kullanım iddia etmez.

1. Doğru GCP projesinde `kgsearch.googleapis.com` etkinleştirilir.
2. Yalnız bu API'ye kısıtlı ayrı standart anahtar oluşturulur. Gemini ve Firebase
   anahtarlarının kısıtları değiştirilmez.
3. Anahtar SSM'de `/geoni/google-kg-key` altında SecureString olarak saklanır;
   API ve worker çalışma ortamlarına `GOOGLE_KG_API_KEY` secret referansı eklenir.
   IAM erişimi gerekiyorsa yalnız bu parametre kapsamına değerlendirilir.
4. Bilinen bir varlık için salt okunur KG sorgusu doğrulanır; sonra yetkili
   dağıtım yapılır. Ücretli tam tarama otomatik başlatılmaz.

Yerel kod değişikliği anahtar/API oluşturmaz, IAM veya üretim ayarı değiştirmez.
Anahtar yoksa/hata varsa sonuç `None` (ölçülmedi), başarılı boş yanıtta
`present: false` kalır. Formül, eşik ve ağırlıklar değişmez. KG verisinin yeniden
ölçülebilmesi mevcut formülün girdisini etkileyebilir; yeni taramalar önceki
eksik ölçümlerden farklı puan üretebilir.

Kaynaklar:
- https://ai.google.dev/gemini-api/docs/api-key
- https://developers.google.com/knowledge-graph/how-tos/authorizing
- https://developers.google.com/knowledge-graph

6 Eylül uygulama güncellemesi: mevcut `yonetmen-app-2026` projesinde legacy
KG API etkinleştirildi. Service Usage API günlük 100.000 ve kullanıcı başına
dakikalık 600 sorgu kotası döndürdü; kota fatura tutarı değildir. Enterprise
ürününe veya yeni aboneliğe geçilmedi. `geoni-kg-search` anahtarı yalnız
`kgsearch.googleapis.com` için kısıtlandı. Başlıkla gönderilen anahtar gerçek
Google varlık sorgusunda HTTP 200 ve eşleşen sonuç döndürdü. İlk çağrının
geçici anahtar hatası sonraki çağrıda kalktı; URL anahtarına dönülmedi.

Anahtar `/geoni/google-kg-key` Standard SecureString içinde; App Runner'a
secret referansı ve worker görev tanımına aynı referans eklendi. Mevcut IAM
erişimi parametreyi kapsadığı için izin genişletilmedi. Worker istenen görev
sayısı sıfır olarak korundu; doğrulama için ücretli tam tarama başlatılmadı.
905 test geçti, 1 test atlandı; tanımsız isim lint kapısı geçti. Canlı commit
ve çalışma davranışı dağıtım sonrasında ayrıca doğrulanır.
