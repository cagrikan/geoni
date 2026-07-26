# GEONI Visibility Scanner

Markaların, kişilerin ve web sitelerinin **AI cevap motorlarındaki** görünürlüğünü
ölçen ve iyileştiren servisin backend'i.

> Bu dosya 2026-07-26'da baştan yazıldı. Öncesinde gün-1 planını (SQLAlchemy +
> PostgreSQL + Redis + docker-compose) anlatıyordu; o mimari **hiç kurulmadı**.
> Yanlış README yeni gelen birini saatlerce yanlış yöne götürüyordu.

## Gerçek mimari

| Katman | Ne kullanılıyor |
|---|---|
| API | FastAPI (`main.py`) — App Runner'da |
| Kuyruk/işçi | SQS + ECS worker (`worker.py`) — uzun taramalar burada |
| Veri | **Supabase (PostgREST üzerinden HTTP)** — `db.py`. ORM YOK, model sınıfı YOK |
| Ölçüm | `brand_recall.py` (4 canlı motor + gölge mod), `sov.py`, `crawler.py` |
| Hizmet otomasyonu | `ticket_automation.py` |
| Öz-gelişim / izleme | `self_improve.py`, `monitor.py` (lifespan içinde döngüler) |

**Yok olan şeyler:** `models.py`, SQLAlchemy, Redis'e bağımlı bir veri katmanı,
docker-compose ile ayağa kalkan yerel Postgres. Redis yalnızca *paylaşımlı hız
sınırı* için opsiyonel (`ratelimit.py`); yoksa bellek içine düşer.

## Yerel çalıştırma

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # Supabase + sağlayıcı anahtarları
uvicorn main:app --reload     # http://localhost:8000
```

Gerçek anahtarlar üretimde **AWS Secrets Manager**'da; App Runner bunları
`RuntimeEnvironmentSecrets` ile enjekte eder. Depoda secret YOKTUR.

## Test

```bash
python -m pytest tests/ -q
```

Testler ağ gerektirmez (HTTP istemcisi sahtelenir). CI'da deploy'dan **önce**
koşar; kırmızı testte deploy durur (`.github/workflows/deploy.yml`).

## Deploy

`main` dalına `geoni-scanner/**` altında bir değişiklik push edilince
GitHub Actions: test → Docker build → ECR → App Runner + ECS worker.
`:latest` yanında `:$SHA` immutable tag da basılır (rollback + denetim izi).

## Dokümantasyon notu

Kök dizindeki `DEPLOYMENT_GUIDE.md`, `DEVELOPMENT_SUMMARY.md`,
`QUICK_ACTION_PLAN.md`, `QUICK_START_REVISED.md`, `SETUP_REVISED.md` dosyaları
da aynı gün-1 planından kalmadır ve **güncel değildir**. Güncel mimari kararlar
kodun içindeki yorumlarda ve proje hafızasında tutulur.
