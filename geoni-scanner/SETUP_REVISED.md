# GEONI Deployment - Revised Setup
## GitHub: cagrikan/geoni/geoni-scanner
## Domain: geoni.ai (Landing page live on Vercel)

---

## 🏗️ Repo Structure

```
github.com/cagrikan/geoni/
├── geoni-landing/          (existing - Vercel deployed)
│   └── Landing page, pricing, docs
│
├── geoni-scanner/          (NEW - backend API)
│   ├── main.py
│   ├── models.py
│   ├── crawler.py
│   ├── ... (all backend files)
│   ├── docker-compose.yml
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── task-definition.json
│   ├── aws-setup.sh
│   ├── .github/
│   │   └── workflows/
│   │       └── deploy.yml
│   └── README.md
│
└── geoni-frontend/         (NEXT - React app)
    └── Vite + React + API integration
```

---

## 🚀 Step 1: Push Backend to GitHub

```bash
# Navigate to geoni-scanner folder
cd ~/path/to/geoni-scanner

# Initialize git (if not already done)
git init

# Copy ALL backend files into this folder
# (main.py, models.py, schemas.py, config.py, database.py,
#  crawler.py, indexing.py, scoring.py, requirements.txt,
#  docker-compose.yml, Dockerfile, aws-setup.sh, etc.)

# Add GitHub remote
git remote add origin https://github.com/cagrikan/geoni.git

# Create and checkout geoni-scanner branch
git checkout -b geoni-scanner

# Stage all files
git add .

# Commit
git commit -m "GEONI Scanner - FastAPI Backend Foundation"

# Push to geoni repo (this will create the directory structure)
git push -u origin geoni-scanner

# After push, GitHub will show:
# github.com/cagrikan/geoni/tree/geoni-scanner
```

---

## ✅ Step 2: GitHub Actions Setup (CI/CD)

### Create GitHub Actions Workflow

Create file: `.github/workflows/deploy-scanner.yml`

```yaml
name: Deploy GEONI Scanner to AWS

on:
  push:
    branches: [main]
    paths:
      - 'geoni-scanner/**'

env:
  AWS_REGION: eu-central-1
  ECR_REPOSITORY: geoni-scanner
  ECS_SERVICE: geoni-scanner-service
  ECS_CLUSTER: geoni-cluster

jobs:
  deploy:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: geoni-scanner

    steps:
      - uses: actions/checkout@v4

      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: ${{ env.AWS_REGION }}

      - name: Login to Amazon ECR
        id: login-ecr
        uses: aws-actions/amazon-ecr-login@v2

      - name: Build, tag, and push image to Amazon ECR
        id: image
        env:
          ECR_REGISTRY: ${{ steps.login-ecr.outputs.registry }}
          IMAGE_TAG: ${{ github.sha }}
        run: |
          docker build -t $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG .
          docker tag $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG $ECR_REGISTRY/$ECR_REPOSITORY:latest
          docker push $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG
          docker push $ECR_REGISTRY/$ECR_REPOSITORY:latest
          echo "image=$ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG" >> $GITHUB_OUTPUT

      - name: Fill in the new image ID in the Amazon ECS task definition
        id: task-def
        uses: aws-actions/amazon-ecs-render-task-definition@v1
        with:
          task-definition: geoni-scanner/task-definition.json
          container-name: geoni-backend
          image: ${{ steps.image.outputs.image }}

      - name: Deploy Amazon ECS task definition
        uses: aws-actions/amazon-ecs-deploy-task-definition@v1
        with:
          task-definition: ${{ steps.task-def.outputs.task-definition }}
          service: ${{ env.ECS_SERVICE }}
          cluster: ${{ env.ECS_CLUSTER }}
          wait-for-service-stability: true

      - name: Slack Notification on Success
        if: success()
        uses: slackapi/slack-github-action@v1.24.0
        with:
          webhook-url: ${{ secrets.SLACK_WEBHOOK }}
          payload: |
            {
              "text": "✅ GEONI Scanner Deployed",
              "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "*✅ Backend Deployed*\nAPI: https://api.geoni.ai/health"}}]
            }

      - name: Slack Notification on Failure
        if: failure()
        uses: slackapi/slack-github-action@v1.24.0
        with:
          webhook-url: ${{ secrets.SLACK_WEBHOOK }}
          payload: |
            {
              "text": "❌ GEONI Scanner Deployment Failed",
              "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "*❌ Deployment Failed*\nCheck: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"}}]
            }
```

---

## 🔑 Step 3: Add GitHub Secrets (For CI/CD)

Go to: `github.com/cagrikan/geoni` → Settings → Secrets and variables → Actions

Add these secrets:

| Secret | Value |
|--------|-------|
| `AWS_ACCESS_KEY_ID` | Your AWS Access Key |
| `AWS_SECRET_ACCESS_KEY` | Your AWS Secret Key |
| `SLACK_WEBHOOK` | (Optional) Your Slack webhook URL |

**Where to get AWS credentials:**
1. AWS Console → IAM → Users
2. Create user: `geoni-ci` with `AmazonEC2ContainerRegistryPowerUser` policy
3. Create access key
4. Copy to GitHub secrets

---

## 🏢 Step 4: AWS Infrastructure Setup

### Automated Setup (Recommended)

```bash
# From geoni-scanner folder
chmod +x aws-setup.sh
./aws-setup.sh
```

This creates:
- ✅ ECR repository (geoni-scanner)
- ✅ RDS PostgreSQL (geoni-postgres)
- ✅ ElastiCache Redis (geoni-redis)
- ✅ ECS Cluster (geoni-cluster)
- ✅ CloudWatch Logs
- ✅ Docker image built & pushed

### Manual AWS Setup (If script fails)

```bash
# 1. Create ECR repository
aws ecr create-repository \
  --repository-name geoni-scanner \
  --region eu-central-1 \
  --image-scanning-configuration scanOnPush=true

# 2. Create RDS (PostgreSQL)
aws rds create-db-instance \
  --db-instance-identifier geoni-postgres \
  --db-instance-class db.t3.micro \
  --engine postgres \
  --engine-version 15.3 \
  --master-username geoni_admin \
  --master-user-password "YOUR_STRONG_PASSWORD" \
  --allocated-storage 20 \
  --no-publicly-accessible \
  --backup-retention-period 7 \
  --region eu-central-1

# 3. Create ElastiCache (Redis)
aws elasticache create-cache-cluster \
  --cache-cluster-id geoni-redis \
  --cache-node-type cache.t3.micro \
  --engine redis \
  --engine-version 7.0 \
  --num-cache-nodes 1 \
  --region eu-central-1

# 4. Create ECS Cluster
aws ecs create-cluster \
  --cluster-name geoni-cluster \
  --region eu-central-1

# 5. Create CloudWatch Log Group
aws logs create-log-group \
  --log-group-name /ecs/geoni-scanner \
  --region eu-central-1

# 6. Build and push Docker image
aws ecr get-login-password --region eu-central-1 | \
  docker login --username AWS --password-stdin YOUR_ECR_REGISTRY

docker build -t YOUR_ECR_REGISTRY/geoni-scanner:latest .
docker push YOUR_ECR_REGISTRY/geoni-scanner:latest
```

---

## 🌐 Step 5: Configure Domain for API

You have `geoni.ai` pointed to Vercel landing page.

For API, create subdomain:

### Option A: AWS Route53 (Recommended)

```bash
# If using Route53 for DNS:
aws route53 list-hosted-zones

# Create record for api.geoni.ai pointing to ALB
aws route53 change-resource-record-sets \
  --hosted-zone-id Z123XYZ \
  --change-batch '{
    "Changes": [{
      "Action": "CREATE",
      "ResourceRecordSet": {
        "Name": "api.geoni.ai",
        "Type": "CNAME",
        "TTL": 300,
        "ResourceRecords": [{"Value": "geoni-alb-xxxxx.eu-central-1.elb.amazonaws.com"}]
      }
    }]
  }'
```

### Option B: Update DNS Provider (Cloudflare, Namecheap, etc)

1. Get ALB DNS name from AWS Console
2. Add DNS record:
   - Name: `api`
   - Type: `CNAME`
   - Value: `geoni-alb-xxxxx.eu-central-1.elb.amazonaws.com`

**Result:** `api.geoni.ai` → AWS ALB → ECS Fargate → Your API ✅

---

## 🎨 Step 6: Frontend Setup (React + Vercel)

### Create Frontend in geoni repo

```bash
# In geoni folder (parent of geoni-scanner)
npm create vite@latest geoni-frontend -- --template react-ts
cd geoni-frontend

npm install axios react-router-dom zustand

# Create environment file
cat > .env.production << 'EOF'
VITE_API_URL=https://api.geoni.ai
VITE_APP_NAME=GEONI
EOF

# Push to GitHub
cd ..
git add geoni-frontend/
git commit -m "Add GEONI Frontend - React + Vite"
git push origin main
```

### Deploy to Vercel

**Option A: Via Vercel CLI**

```bash
cd geoni-frontend
npm i -g vercel
vercel
# Follow prompts
# Set Environment Variable: VITE_API_URL = https://api.geoni.ai
# Deploy!
```

**Option B: Via Vercel UI**

1. Go to `vercel.com/dashboard`
2. Import project → Select `geoni-frontend` from `github.com/cagrikan/geoni`
3. Framework: Vite
4. Root directory: `geoni-frontend`
5. Environment: `VITE_API_URL = https://api.geoni.ai`
6. Deploy

**Result:** Frontend live at `https://app.geoni.ai` or `https://geoni.vercel.app`

---

## 📧 Step 7: Resend Email Integration

### Setup Resend

1. Create account: https://resend.com
2. Get API key from dashboard
3. Verify domain: `geoni.ai`

### Add to GitHub Secrets

Go to: `github.com/cagrikan/geoni` → Secrets

Add: `RESEND_API_KEY = re_xxxxxxxxxxxxx`

### Add to Backend Code

Update `main.py`:

```python
from resend import Resend

resend_client = Resend(api_key=settings.RESEND_API_KEY)

# In audit completion:
async def run_audit_pipeline(...):
    # ... audit logic ...
    
    if audit.status == AuditStatus.COMPLETE.value:
        try:
            resend_client.emails.send({
                "from": "noreply@geoni.ai",
                "to": user.email,
                "subject": f"Your AI Visibility Score: {audit.overall_score}/100",
                "html": f"""
                <h2>AI Visibility Audit Results</h2>
                <p>Domain: {audit.domain}</p>
                <h1 style="color: #2E75B6; font-size: 48px;">{audit.overall_score}/100</h1>
                <p><a href="https://geoni.ai/audit/{audit.id}">View Full Report</a></p>
                """
            })
        except Exception as e:
            logger.error(f"Failed to send email: {e}")
```

### Push to GitHub (Auto-deploys)

```bash
cd geoni-scanner
git add .
git commit -m "Add Resend email integration"
git push origin main
# → GitHub Actions automatically deploys to AWS
```

---

## 🚀 Step 8: End-to-End Testing

### Test Backend API

```bash
# Health check
curl https://api.geoni.ai/health

# Get API docs
open https://api.geoni.ai/docs

# Run test audit
curl -X POST https://api.geoni.ai/api/audit/quick \
  -H "Content-Type: application/json" \
  -d '{
    "domain": "example.com",
    "email": "test@cagrikan.com",
    "page_limit": 50
  }'
```

### Test Frontend

```bash
open https://app.geoni.ai
# or
open https://geoni.vercel.app
```

### Test Complete Flow

1. Go to frontend: `https://app.geoni.ai`
2. Enter domain: `example.com`
3. Enter email: `test@cagrikan.com`
4. Click "Run Audit"
5. Wait 3-5 minutes
6. Check email for results
7. View results dashboard

---

## 📊 Step 9: Monitoring & Logs

### View Backend Logs

```bash
# Real-time logs
aws logs tail /ecs/geoni-scanner --follow

# Search for errors
aws logs filter-log-events \
  --log-group-name /ecs/geoni-scanner \
  --filter-pattern "ERROR"

# CloudWatch Dashboard
open https://console.aws.amazon.com/cloudwatch/
```

### Check Service Status

```bash
# ECS service status
aws ecs describe-services \
  --cluster geoni-cluster \
  --services geoni-scanner-service \
  --region eu-central-1

# Get running tasks
aws ecs list-tasks \
  --cluster geoni-cluster \
  --service-name geoni-scanner-service
```

### Monitor Database

```bash
# RDS status
aws rds describe-db-instances \
  --db-instance-identifier geoni-postgres

# Connect to database
psql -h geoni-postgres.xxxxx.eu-central-1.rds.amazonaws.com \
  -U geoni_admin \
  -d geoni_scanner
```

---

## 🎯 Daily Operations

### Monitor (5 min/day)

```bash
# Quick health check
curl https://api.geoni.ai/health

# Check for errors
aws logs tail /ecs/geoni-scanner --follow --max-items 20

# View metrics
aws cloudwatch get-metric-statistics \
  --namespace ECS/ContainerInsights \
  --metric-name CPUUtilized \
  --dimensions Name=ServiceName,Value=geoni-scanner-service \
  --statistics Average \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300
```

### Deploy Changes (2 min)

```bash
# Make code changes
nano geoni-scanner/crawler.py

# Commit and push
git add geoni-scanner/
git commit -m "Improve crawler performance"
git push origin main

# → GitHub Actions automatically:
# 1. Builds Docker image
# 2. Pushes to ECR
# 3. Updates ECS service
# 4. Restarts containers with new code
# (New version live in ~2 minutes)
```

### Scale Resources (If needed)

```bash
# Increase ECS task count
aws ecs update-service \
  --cluster geoni-cluster \
  --service geoni-scanner-service \
  --desired-count 4

# Upgrade RDS instance
aws rds modify-db-instance \
  --db-instance-identifier geoni-postgres \
  --db-instance-class db.t3.small
```

---

## 💰 Monthly Cost Breakdown

| Service | Cost | Notes |
|---------|------|-------|
| RDS PostgreSQL | $15 | db.t3.micro + 20GB storage |
| ElastiCache Redis | $15 | cache.t3.micro |
| ECS Fargate (2 tasks) | $30-50 | 256 CPU, 512 MB |
| ALB | $16 | Load balancer + data |
| Data transfer | $50 | ~1-2 TB |
| Resend (email) | $20 | 100 free + $0.10/email |
| Vercel | $0-20 | Pro plan (~$20) |
| GitHub | Free | Unlimited private repos |
| **Total** | **~$150-170/month** | Scales with users |

---

## 🔑 Quick Reference Commands

```bash
# GitHub - Push code
git push origin main

# AWS - Check status
aws ecs describe-services --cluster geoni-cluster --services geoni-scanner-service

# AWS - View logs
aws logs tail /ecs/geoni-scanner --follow

# AWS - Restart service
aws ecs update-service --cluster geoni-cluster --service geoni-scanner-service --force-new-deployment

# Vercel - Check deployment
vercel --prod

# Database - Connect
psql -h geoni-postgres.xxxxx.eu-central-1.rds.amazonaws.com -U geoni_admin -d geoni_scanner

# API - Health
curl https://api.geoni.ai/health
```

---

## ✅ Launch Checklist

- [ ] Backend files in `geoni-scanner` folder
- [ ] `.github/workflows/deploy-scanner.yml` created
- [ ] GitHub secrets added (AWS keys, Resend key)
- [ ] `git push origin main` from geoni-scanner
- [ ] AWS infrastructure created (ECR, RDS, Redis, ECS)
- [ ] Docker image in ECR
- [ ] ECS service started
- [ ] ALB pointing to `api.geoni.ai`
- [ ] Backend responsive at `https://api.geoni.ai/health`
- [ ] Frontend deployed to Vercel
- [ ] Frontend configured with `VITE_API_URL=https://api.geoni.ai`
- [ ] Frontend loads at `https://app.geoni.ai`
- [ ] Resend API key in GitHub secrets
- [ ] First audit completes end-to-end
- [ ] Email received with results
- [ ] CloudWatch monitoring configured

---

## 🎉 You're Production Ready!

```
github.com/cagrikan/geoni/
├── geoni-landing     → https://geoni.ai (Vercel, live)
├── geoni-scanner     → https://api.geoni.ai (AWS, live)
├── geoni-frontend    → https://app.geoni.ai (Vercel, live)
└── .github/workflows → Auto-deploy on push
```

**Full production stack, zero manual deployment, auto-scaling, email notifications. Ready to take users!** 🚀

---

## 🚨 Troubleshooting

| Issue | Solution |
|-------|----------|
| API not responding | `aws ecs update-service --force-new-deployment` |
| GitHub Actions fails | Check AWS secrets in GitHub |
| Database connection error | Wait 2 min for RDS to start, check password |
| Emails not sending | Check Resend API key, verify domain |
| High costs | Scale down ECS task count or RDS instance |
| Frontend can't reach API | Check CORS in backend, check `VITE_API_URL` |

---

**Questions? Check DEPLOYMENT_GUIDE.md for detailed explanations.**
