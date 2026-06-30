# GEONI Deployment Strategy
## Using Vercel + AWS + Resend + GitHub

**Timeline:** Week 4-5 (After backend testing complete)

---

## 🏗️ Full Stack Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     USERS (Browser)                          │
└────────────────┬────────────────────────────┬────────────────┘
                 │                            │
        ┌────────▼──────────┐      ┌──────────▼────────┐
        │   VERCEL (CDN)    │      │  RESEND (Email)   │
        │  React Frontend   │      │   Audit Reports   │
        │  geoni.ai         │      │   Welcome Emails  │
        └────────┬──────────┘      └───────────────────┘
                 │
        ┌────────▼──────────────────────────┐
        │      AWS (Backend Services)       │
        │                                   │
        │  ┌─────────────────────────────┐  │
        │  │  ECS Fargate (FastAPI)      │  │
        │  │  geoni-scanner backend      │  │
        │  └──────────┬────────────────┬─┘  │
        │             │                │    │
        │  ┌──────────▼───┐  ┌────────▼──┐ │
        │  │ RDS Postgres │  │ ElastiCache
        │  │  (Audits DB) │  │  (Redis)   │ │
        │  └──────────────┘  └───────────┘ │
        │                                   │
        └───────────────────────────────────┘
                     │
        ┌────────────▼──────────┐
        │   GitHub Actions      │
        │   (CI/CD Pipeline)    │
        └───────────────────────┘
```

---

## 📋 Deployment Checklist (Week 4-5)

### **Phase 1: Setup GitHub + CI/CD (1 day)**

- [ ] Create GitHub repo (private)
- [ ] Push all backend files
- [ ] Setup GitHub Actions workflow
- [ ] Test local build in Actions

### **Phase 2: AWS Backend Deployment (2 days)**

- [ ] Create ECR repository
- [ ] Create RDS PostgreSQL instance
- [ ] Create ElastiCache Redis cluster
- [ ] Create ECS Fargate service
- [ ] Setup load balancer (ALB)
- [ ] Configure security groups
- [ ] Setup CloudWatch monitoring
- [ ] Test API endpoints

### **Phase 3: Frontend + Vercel (1 day)**

- [ ] Create React frontend (Vite)
- [ ] Connect Vercel to GitHub
- [ ] Deploy frontend
- [ ] Configure environment variables
- [ ] Point domain to Vercel

### **Phase 4: Email Integration (1 day)**

- [ ] Create Resend account
- [ ] Get API key
- [ ] Integrate into backend
- [ ] Test email sending
- [ ] Setup email templates

### **Phase 5: Monitoring & Launch (1 day)**

- [ ] Setup CloudWatch alarms
- [ ] Test end-to-end flow
- [ ] Beta test with ARD team
- [ ] Go live

---

## 🚀 **Step 1: GitHub Setup**

### Create Repository

```bash
# Initialize git locally
cd geoni-scanner
git init
git add .
git commit -m "GEONI MVP Week 1 - Backend foundation"

# Create repo on GitHub (private)
# https://github.com/new → Name: geoni-scanner

# Push to GitHub
git remote add origin https://github.com/YOUR_USERNAME/geoni-scanner.git
git branch -M main
git push -u origin main
```

### Setup GitHub Actions (CI/CD)

Create `.github/workflows/deploy.yml`:

```yaml
name: Deploy to AWS

on:
  push:
    branches: [main]

env:
  AWS_REGION: eu-central-1
  ECR_REPOSITORY: geoni-scanner
  ECS_SERVICE: geoni-scanner-service
  ECS_CLUSTER: geoni-cluster

jobs:
  deploy:
    runs-on: ubuntu-latest
    
    steps:
      - uses: actions/checkout@v3
      
      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v2
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: ${{ env.AWS_REGION }}
      
      - name: Login to Amazon ECR
        id: login-ecr
        uses: aws-actions/amazon-ecr-login@v1
      
      - name: Build, tag, and push image to Amazon ECR
        env:
          ECR_REGISTRY: ${{ steps.login-ecr.outputs.registry }}
          IMAGE_TAG: ${{ github.sha }}
        run: |
          docker build -t $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG .
          docker push $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG
      
      - name: Update ECS service
        run: |
          aws ecs update-service \
            --cluster $ECS_CLUSTER \
            --service $ECS_SERVICE \
            --force-new-deployment
```

Add GitHub Secrets:
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`

---

## 🏢 **Step 2: AWS Deployment**

### 2a. Create ECR Repository

```bash
aws ecr create-repository \
  --repository-name geoni-scanner \
  --region eu-central-1
```

### 2b. Create RDS PostgreSQL

```bash
aws rds create-db-instance \
  --db-instance-identifier geoni-postgres \
  --db-instance-class db.t3.micro \
  --engine postgres \
  --master-username geoni_admin \
  --master-user-password <STRONG_PASSWORD> \
  --allocated-storage 20 \
  --vpc-security-group-ids sg-xxxxx \
  --no-publicly-accessible \
  --backup-retention-period 7
```

**Output:** Note the `Endpoint` (e.g., `geoni-postgres.xxxxx.eu-central-1.rds.amazonaws.com`)

### 2c. Create ElastiCache Redis

```bash
aws elasticache create-cache-cluster \
  --cache-cluster-id geoni-redis \
  --cache-node-type cache.t3.micro \
  --engine redis \
  --num-cache-nodes 1 \
  --vpc-security-group-ids sg-xxxxx
```

**Output:** Note the `CacheNodes[0].Endpoint.Address`

### 2d. Create ECS Cluster & Service

```bash
# Create cluster
aws ecs create-cluster --cluster-name geoni-cluster

# Create task definition
aws ecs register-task-definition \
  --family geoni-scanner \
  --network-mode awsvpc \
  --requires-compatibilities FARGATE \
  --cpu 256 \
  --memory 512 \
  --container-definitions '[
    {
      "name": "geoni-backend",
      "image": "<ECR_REGISTRY>/geoni-scanner:latest",
      "portMappings": [
        {"containerPort": 8000, "hostPort": 8000, "protocol": "tcp"}
      ],
      "environment": [
        {"name": "DATABASE_URL", "value": "postgresql://geoni_admin:<PASSWORD>@geoni-postgres.xxxxx.eu-central-1.rds.amazonaws.com:5432/geoni_scanner"},
        {"name": "REDIS_URL", "value": "redis://geoni-redis.xxxxx.cache.amazonaws.com:6379/0"},
        {"name": "DEBUG", "value": "false"},
        {"name": "LOG_LEVEL", "value": "INFO"}
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/geoni-scanner",
          "awslogs-region": "eu-central-1",
          "awslogs-stream-prefix": "ecs"
        }
      }
    }
  ]'

# Create service
aws ecs create-service \
  --cluster geoni-cluster \
  --service-name geoni-scanner-service \
  --task-definition geoni-scanner:1 \
  --desired-count 2 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-xxxxx],securityGroups=[sg-xxxxx],assignPublicIp=ENABLED}"
```

### 2e. Setup Application Load Balancer

```bash
# Create ALB
aws elbv2 create-load-balancer \
  --name geoni-alb \
  --subnets subnet-xxxxx subnet-yyyyy \
  --security-groups sg-xxxxx

# Create target group
aws elbv2 create-target-group \
  --name geoni-targets \
  --protocol HTTP \
  --port 8000 \
  --vpc-id vpc-xxxxx

# Register targets with ECS service
# (Done automatically by ECS)
```

---

## 🎨 **Step 3: Frontend (React + Vercel)**

### Create React App

```bash
# Create frontend in same directory
npm create vite@latest geoni-frontend -- --template react-ts

cd geoni-frontend
npm install

# Install dependencies
npm install axios react-router-dom zustand
```

### Key Frontend Files

Create `src/api/client.ts`:

```typescript
import axios from 'axios';

const API_URL = import.meta.env.VITE_API_URL || 'https://api.geoni.ai';

export const api = axios.create({
  baseURL: API_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

export const startAudit = (domain: string, email: string) => {
  return api.post('/api/audit/quick', { domain, email, page_limit: 500 });
};

export const getAuditStatus = (jobId: string) => {
  return api.get(`/api/audit/${jobId}`);
};

export const getAuditResults = (jobId: string) => {
  return api.get(`/api/audit/${jobId}/results`);
};
```

Create `src/pages/AuditForm.tsx`:

```typescript
import { useState } from 'react';
import { startAudit, getAuditStatus } from '../api/client';

export default function AuditForm() {
  const [domain, setDomain] = useState('');
  const [email, setEmail] = useState('');
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState('');
  const [results, setResults] = useState<any>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const response = await startAudit(domain, email);
      setJobId(response.data.job_id);
      
      // Start polling
      pollStatus(response.data.job_id);
    } catch (error) {
      console.error('Error starting audit:', error);
    }
  };

  const pollStatus = async (id: string) => {
    const interval = setInterval(async () => {
      try {
        const response = await getAuditStatus(id);
        setStatus(response.data.status);
        
        if (response.data.status === 'complete') {
          clearInterval(interval);
          // Fetch results
          const resultsResponse = await getAuditResults(id);
          setResults(resultsResponse.data);
        }
      } catch (error) {
        console.error('Error polling status:', error);
      }
    }, 5000); // Poll every 5 seconds
  };

  return (
    <div className="max-w-2xl mx-auto p-8">
      <h1 className="text-4xl font-bold mb-8">
        Check Your AI Visibility
      </h1>

      {!jobId ? (
        <form onSubmit={handleSubmit} className="space-y-4">
          <input
            type="text"
            placeholder="example.com"
            value={domain}
            onChange={(e) => setDomain(e.target.value)}
            className="w-full p-3 border rounded"
            required
          />
          <input
            type="email"
            placeholder="your@email.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full p-3 border rounded"
            required
          />
          <button
            type="submit"
            className="w-full bg-blue-600 text-white p-3 rounded font-bold"
          >
            Run Free Audit
          </button>
        </form>
      ) : (
        <div className="space-y-4">
          <p className="text-xl">Status: <strong>{status}</strong></p>
          
          {results && (
            <div className="bg-gray-50 p-6 rounded">
              <h2 className="text-3xl font-bold mb-4">
                AI Visibility Score: {results.overall_score}/100
              </h2>
              <p>Pages Crawled: {results.total_pages_crawled}</p>
              <p>Pages Indexed: {results.total_pages_indexed}</p>
              
              {results.top_performing_topics && (
                <div className="mt-4">
                  <h3 className="font-bold mb-2">Top Topics:</h3>
                  {results.top_performing_topics.map((t: any, i: number) => (
                    <p key={i}>• {t.topic_name} ({t.mention_count} mentions)</p>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
```

### Deploy to Vercel

```bash
# Install Vercel CLI
npm i -g vercel

# Deploy
vercel

# Follow prompts, then setup environment variables:
# VITE_API_URL = https://api.geoni.ai
```

Or connect GitHub to Vercel UI:
1. Go to `vercel.com`
2. Import project from GitHub
3. Set environment variables
4. Deploy

---

## 📧 **Step 4: Email Integration (Resend)**

### Setup Resend

1. Create account: https://resend.com
2. Get API key from dashboard
3. Verify domain (geoni.ai)

### Add to Backend

Install dependency:

```bash
pip install resend
```

Create `email.py`:

```python
from resend import Resend

resend = Resend(api_key="re_xxxxx")

def send_audit_results(email: str, domain: str, score: int):
    """Send audit results via email."""
    resend.emails.send(
        {
            "from": "noreply@geoni.ai",
            "to": email,
            "subject": f"Your AI Visibility Score: {score}/100",
            "html": f"""
            <h2>AI Visibility Audit Results</h2>
            <p>Domain: {domain}</p>
            <h1 style="color: #2E75B6; font-size: 48px;">{score}/100</h1>
            <p>See full results: <a href="https://geoni.ai/audit/{domain}">View Report</a></p>
            """
        }
    )

def send_welcome_email(email: str):
    """Send welcome email to new user."""
    resend.emails.send(
        {
            "from": "welcome@geoni.ai",
            "to": email,
            "subject": "Welcome to GEONI",
            "html": """
            <h2>Welcome to GEONI</h2>
            <p>We're excited to help you improve your AI visibility.</p>
            <p>Start your first free audit: <a href="https://geoni.ai">geoni.ai</a></p>
            """
        }
    )
```

Integrate into `main.py`:

```python
from email import send_audit_results, send_welcome_email

# In run_audit_pipeline, after completion:
if audit.status == AuditStatus.COMPLETE.value:
    try:
        send_audit_results(user.email, audit.domain, audit.overall_score)
    except Exception as e:
        logger.error(f"Failed to send email: {e}")

# In start_audit, for new users:
if not user:  # New user
    user = User(...)
    db.add(user)
    db.commit()
    send_welcome_email(user.email)
```

---

## 📊 **Step 5: Monitoring & Operations**

### CloudWatch Dashboard

```bash
# View logs
aws logs tail /ecs/geoni-scanner --follow

# Create alarm for errors
aws cloudwatch put-metric-alarm \
  --alarm-name geoni-errors \
  --alarm-description "Alert on backend errors" \
  --metric-name ErrorCount \
  --namespace ECS/ContainerInsights \
  --statistic Sum \
  --period 300 \
  --threshold 10 \
  --comparison-operator GreaterThanThreshold
```

### Health Checks

```bash
# Test API endpoint
curl https://api.geoni.ai/health

# Test database
aws rds describe-db-instances --db-instance-identifier geoni-postgres

# Test Redis
aws elasticache describe-cache-clusters --cache-cluster-id geoni-redis
```

---

## 📋 **Environment Variables (Production)**

Set in AWS ECS Task Definition + Vercel:

**Backend (.env):**
```bash
DEBUG=false
LOG_LEVEL=WARNING
DATABASE_URL=postgresql://geoni_admin:PASSWORD@geoni-postgres.xxxxx.eu-central-1.rds.amazonaws.com:5432/geoni_scanner
REDIS_URL=redis://geoni-redis.xxxxx.cache.amazonaws.com:6379/0
RESEND_API_KEY=re_xxxxx
CORS_ORIGINS=["https://geoni.ai","https://app.geoni.ai"]
JWT_SECRET_KEY=<GENERATE_STRONG_KEY>
```

**Frontend (.env.production):**
```bash
VITE_API_URL=https://api.geoni.ai
```

---

## 🔐 **Security Checklist**

- [ ] RDS: Disable public access
- [ ] Redis: Use security groups (no public access)
- [ ] ECR: Enable image scanning
- [ ] ECS: Run as non-root user
- [ ] ALB: HTTPS only (SSL certificate via ACM)
- [ ] Database: Enable encryption at rest
- [ ] Secrets: Use AWS Secrets Manager for sensitive data
- [ ] API: Rate limiting enabled
- [ ] CORS: Restricted to geoni.ai domain

---

## 💰 **Cost Estimate (Monthly)**

| Service | Instance | Cost |
|---------|----------|------|
| RDS PostgreSQL | db.t3.micro | $15 |
| ElastiCache Redis | cache.t3.micro | $15 |
| ECS Fargate | 2×256 CPU, 512MB | $30 |
| Load Balancer | ALB | $16 |
| Data Transfer | ~1TB | $50 |
| **Total** | | **~$130/month** |

---

## 🚀 **Launch Timeline**

```
Week 4:
├─ Day 1: GitHub + CI/CD setup
├─ Day 2: AWS infrastructure (RDS, Redis, ECS)
└─ Day 3: ALB + Route53 DNS

Week 5:
├─ Day 1: React frontend scaffolding
├─ Day 2: Frontend + Vercel deployment
├─ Day 3: Resend email integration
└─ Day 4: End-to-end testing + ARD beta

Week 6 (if needed):
└─ Performance optimization + monitoring
```

---

## 📞 Quick Reference

### Push Code to GitHub
```bash
git add .
git commit -m "Feature description"
git push origin main
# → GitHub Actions automatically builds & deploys
```

### Check AWS Deployment
```bash
aws ecs describe-services --cluster geoni-cluster --services geoni-scanner-service
aws logs tail /ecs/geoni-scanner --follow
```

### Monitor Vercel
```bash
vercel env ls
vercel logs
```

### Test API
```bash
curl https://api.geoni.ai/health
curl https://api.geoni.ai/docs
```

---

**Özet:** GitHub'a push → GitHub Actions builds Docker image → ECR'a push → ECS auto-updates → Production live ✅
