# GEONI Visibility Scanner MVP

**Free AI visibility auditing tool for brands.** Measures and tracks brand visibility across ChatGPT, Perplexity, Gemini, and Google AI.

---

## 🚀 Quick Start

### Prerequisites
- Docker & Docker Compose
- Python 3.11+ (for local development)
- Git

### Setup & Run

#### Option 1: Docker Compose (Recommended)

```bash
# Clone repository
git clone <repo-url>
cd geoni-scanner

# Copy environment file
cp .env.example .env

# Start services (PostgreSQL, Redis, FastAPI)
docker-compose up -d

# Check logs
docker-compose logs -f backend
```

**Backend will be available at:** `http://localhost:8000`

**API Documentation:**
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

#### Option 2: Local Development

```bash
# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium

# Start PostgreSQL & Redis (separately or using Docker)
docker-compose up -d postgres redis

# Copy environment file
cp .env.example .env

# Update .env with local database URL
DATABASE_URL="postgresql://geoni_user:geoni_password@localhost:5432/geoni_scanner"

# Run FastAPI server
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

---

## 📁 Project Structure

```
geoni-scanner/
├── main.py                 # FastAPI application & endpoints
├── models.py              # SQLAlchemy ORM models
├── schemas.py             # Pydantic request/response schemas
├── config.py              # Configuration management
├── database.py            # Database & Redis connection
├── crawler.py             # Playwright web crawler
├── indexing.py            # Search engine indexing checker
├── scoring.py             # Visibility score calculation
├── requirements.txt       # Python dependencies
├── docker-compose.yml     # Docker compose configuration
├── Dockerfile             # Backend container definition
├── .env.example           # Environment template
└── README.md              # This file
```

---

## 🔄 API Endpoints

### Health Check
```bash
GET /
GET /health
```

### Audit Operations
```bash
# Start an audit
POST /api/audit/quick
{
  "domain": "example.com",
  "email": "user@company.com",
  "competitors": ["competitor.com"],
  "page_limit": 500
}

# Get audit status
GET /api/audit/{job_id}

# Get complete audit results
GET /api/audit/{job_id}/results

# Get cached score
GET /api/score/{domain}
```

### Example Workflow

```bash
# 1. Start audit
curl -X POST http://localhost:8000/api/audit/quick \
  -H "Content-Type: application/json" \
  -d '{
    "domain": "example.com",
    "email": "test@example.com",
    "page_limit": 100
  }'

# Response:
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued",
  "domain": "example.com",
  "created_at": "2026-06-30T10:00:00Z"
}

# 2. Poll status
curl http://localhost:8000/api/audit/550e8400-e29b-41d4-a716-446655440000

# 3. Get results (when status is "complete")
curl http://localhost:8000/api/audit/550e8400-e29b-41d4-a716-446655440000/results
```

---

## 📊 Audit Pipeline

```
Domain Input
    ↓
Crawl (Playwright) → Extract pages, titles, schema
    ↓
Check Indexing → Google, Bing, OpenAI, Anthropic status
    ↓
Compute Score → Weighted algorithm (0-100)
    ↓
Extract Topics → Performing & opportunity topics
    ↓
Store Results → Database & cache
    ↓
Return to User
```

---

## 🏗️ Database Schema

### Key Tables

- **users**: User accounts & subscription tiers
- **audits**: Audit jobs & results
- **pages**: Crawled pages from each domain
- **visibility_scores**: Platform-specific scores
- **topics**: Top performing & opportunity topics
- **citations**: Domains citing the target domain

### Views & Indices

All tables include appropriate indices for fast querying:
- `audits(user_id, domain, status, created_at)`
- `pages(audit_id, url)`
- `visibility_scores(audit_id, platform)`
- `topics(audit_id, category)`

---

## 🎯 Development Workflow

### Phase 1: Core Skeleton (Weeks 1-3)
- ✅ FastAPI setup with database models
- ✅ Crawler & indexing modules
- ✅ Scoring engine
- ⏳ End-to-end testing

### Phase 2: Crawler Enhancement (Weeks 4-6)
- Playwright optimization
- Robots.txt & sitemap parsing
- Performance benchmarking

### Phase 3: Indexing & APIs (Weeks 4-6)
- Google Search Console API integration
- Bing Webmaster Tools integration
- OpenAI/Anthropic robots.txt analysis

### Phase 4: Frontend (Weeks 4-6)
- React audit form
- Results dashboard
- PDF export

### Phase 5: Deployment (Week 7)
- AWS ECR/ECS setup
- RDS PostgreSQL
- ElastiCache Redis
- CloudWatch monitoring

---

## 🔧 Configuration

### Environment Variables

See `.env.example` for all available options. Key variables:

```bash
# Database
DATABASE_URL=postgresql://user:pass@host:5432/dbname

# Redis
REDIS_URL=redis://host:6379/0

# Crawler
CRAWLER_MAX_PAGES=500
CRAWLER_TIMEOUT_PER_PAGE=10

# External APIs
ANTHROPIC_API_KEY=sk-...
OPENAI_API_KEY=sk-...
```

---

## 🧪 Testing

### Unit Tests
```bash
pytest -v
```

### Coverage
```bash
pytest --cov=. --cov-report=html
```

### Load Testing
```bash
locust -f locustfile.py --host=http://localhost:8000
```

---

## 📈 Performance Targets

| Metric | Target |
|--------|--------|
| Audit completion | < 5 min (500 URLs) |
| Dashboard load | < 2s |
| API response | < 200ms |
| Crawler throughput | 2-3 pages/sec |

---

## 🛠️ Troubleshooting

### Database connection error
```bash
# Check PostgreSQL is running
docker-compose ps

# View logs
docker-compose logs postgres
```

### Redis connection error
```bash
# Restart Redis
docker-compose restart redis

# Test connection
redis-cli -u redis://localhost:6379/0 ping
```

### Playwright timeout
- Increase `CRAWLER_TIMEOUT_PER_PAGE` in `.env`
- Check system resources (memory, CPU)
- Verify network connectivity

### High memory usage
- Reduce `CRAWLER_MAX_PAGES`
- Increase crawler delays
- Monitor with `docker stats`

---

## 📚 API Documentation

Auto-generated API docs available at:
- **Swagger UI:** `http://localhost:8000/docs`
- **ReDoc:** `http://localhost:8000/redoc`

Interactive testing available directly in Swagger UI.

---

## 🚢 Deployment

### AWS Deployment Checklist

- [ ] Set up ECR repository
- [ ] Build & push Docker image
- [ ] Configure RDS PostgreSQL
- [ ] Configure ElastiCache Redis
- [ ] Set up ECS Fargate cluster
- [ ] Configure ALB & security groups
- [ ] Set CloudWatch alarms
- [ ] Enable VPC flow logs

### Environment Variables (Production)

```bash
DEBUG=false
LOG_LEVEL=WARNING
DATABASE_URL=<RDS_ENDPOINT>
REDIS_URL=<ELASTICACHE_ENDPOINT>
CORS_ORIGINS=["https://geoni.ai", "https://app.geoni.ai"]
JWT_SECRET_KEY=<GENERATE_SECURE_KEY>
```

---

## 📝 Contributing

1. Create feature branch: `git checkout -b feature/amazing-feature`
2. Commit changes: `git commit -m 'Add amazing feature'`
3. Push to branch: `git push origin feature/amazing-feature`
4. Open Pull Request

---

## 📄 License

Proprietary - GEONI 2026

---

## 💬 Support

Questions? Issues? 
- Create an issue in GitHub
- Contact: support@geoni.ai
- Slack: #geoni-scanner channel

---

**Built with:** FastAPI • SQLAlchemy • PostgreSQL • Redis • Playwright • Pydantic
