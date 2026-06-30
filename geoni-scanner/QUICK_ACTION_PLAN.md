# 🚀 GEONI: From Code to Live - Action Plan

**Your Tools:** Vercel • AWS • Resend • GitHub  
**Your Goal:** Production-ready AI visibility auditing platform  
**Timeline:** 2-3 weeks (Weeks 4-6 of project)

---

## 📋 Complete Checklist: Step-by-Step

### **WEEK 4: GitHub + AWS Setup (3 days)**

#### **Day 1: GitHub Repository**

```bash
# 1. Create private repo on GitHub
# → https://github.com/new
# → Name: geoni-scanner
# → Private: YES

# 2. Initialize locally
cd geoni-scanner
git init

# 3. Copy all files from outputs folder here

# 4. Push to GitHub
git add .
git commit -m "GEONI MVP Week 1 - Backend foundation"
git remote add origin https://github.com/YOUR_USERNAME/geoni-scanner.git
git branch -M main
git push -u origin main
```

**What happens:** Code is now on GitHub, CI/CD pipeline ready.

---

#### **Day 2: AWS Infrastructure (Automated)**

```bash
# 1. Install AWS CLI if not already
# → https://aws.amazon.com/cli/

# 2. Configure AWS credentials
aws configure
# Enter: Access Key ID, Secret Access Key, Region (eu-central-1)

# 3. Make setup script executable
chmod +x aws-setup.sh

# 4. Run automated setup
./aws-setup.sh
```

**What this creates automatically:**
- ✅ ECR repository (Docker image storage)
- ✅ RDS PostgreSQL instance (database)
- ✅ ElastiCache Redis (caching)
- ✅ ECS Cluster (container orchestration)
- ✅ CloudWatch Logs (monitoring)
- ✅ Builds & pushes Docker image

**Output:** Save the database password! You'll need it.

---

#### **Day 2-3: GitHub Secrets Setup**

```bash
# Add secrets to GitHub for CI/CD automation

# 1. Go to GitHub: Settings → Secrets and variables → Actions

# 2. Add 2 required secrets:
AWS_ACCESS_KEY_ID = <copy from AWS IAM>
AWS_SECRET_ACCESS_KEY = <copy from AWS IAM>

# 3. Optional: Add Slack webhook for notifications
SLACK_WEBHOOK = https://hooks.slack.com/services/...
```

**What happens:** When you push to main → GitHub Actions automatically builds & deploys to AWS.

---

### **WEEK 5: Frontend + Email (3 days)**

#### **Day 1: Create React Frontend**

```bash
# 1. Create frontend app in same parent directory
npm create vite@latest geoni-frontend -- --template react-ts
cd geoni-frontend
npm install

# 2. Install dependencies
npm install axios react-router-dom zustand

# 3. Copy UI components from this guide
# → Create src/pages/AuditForm.tsx
# → Create src/api/client.ts

# 4. Create .env.production
VITE_API_URL=https://api.geoni.ai

# 5. Push to separate repo
cd ..
git clone https://github.com/YOUR_USERNAME/geoni-frontend.git
cd geoni-frontend
# Copy all files
git add .
git commit -m "GEONI Frontend - React UI"
git push origin main
```

---

#### **Day 2: Deploy to Vercel**

```bash
# Option A: Via CLI
npm i -g vercel
vercel

# Option B: Via Vercel UI
# 1. Go to https://vercel.com/dashboard
# 2. "Add New..." → "Project"
# 3. Select geoni-frontend repo from GitHub
# 4. Add Environment Variables:
#    VITE_API_URL = https://api.geoni.ai
# 5. Deploy

# Result: Frontend live at https://geoni.vercel.app
# (or your custom domain)
```

---

#### **Day 3: Email Integration (Resend)**

```bash
# 1. Create Resend account
# → https://resend.com (sign up)

# 2. Get API key from dashboard

# 3. Add to GitHub secrets:
RESEND_API_KEY = re_xxxxxxxxxxxxx

# 4. Add to backend code (see DEPLOYMENT_GUIDE.md)
# → pip install resend
# → Create email.py with send functions
# → Integrate into main.py

# 5. Deploy
git add .
git commit -m "Add email integration"
git push origin main
# → GitHub Actions automatically deploys to AWS
```

---

### **WEEK 6: Testing + Launch (2 days)**

#### **Day 1: End-to-End Testing**

```bash
# 1. Test API (AWS)
curl https://api.geoni.ai/health
# → Should return: {"status": "healthy", "version": "0.1.0"}

# 2. Test Frontend (Vercel)
open https://geoni.vercel.app
# → Should load audit form

# 3. Run complete audit
# - Enter domain: example.com
# - Enter email: test@example.com
# - Click "Run Audit"
# - Wait 3-5 minutes
# - Check email for results
# - View results dashboard

# 4. Monitor logs
aws logs tail /ecs/geoni-scanner --follow
```

---

#### **Day 2: ARD Team Beta + Launch**

```bash
# 1. Send to ARD team
# "Hey, audit your domain here: https://geoni.ai"

# 2. Collect feedback
# → Does it work?
# → Are scores reasonable?
# → Is UI intuitive?
# → Email delivery working?

# 3. Monitor AWS dashboard
# → ECS service status
# → Database performance
# → Error rates
# → User metrics

# 4. Go live (if no critical issues)
# → Announce on LinkedIn
# → Send to ARD network
# → Monitor metrics
```

---

## 🔄 **How It All Works Together**

```
┌─────────────────────────────────────────────────────────┐
│  Developer                                               │
│  ├─ Edits code locally                                 │
│  └─ git push origin main                                │
└────────────────┬────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────┐
│  GitHub (Code Repository)                                │
│  ├─ Receives push                                        │
│  └─ Triggers Actions workflow                            │
└────────────────┬────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────┐
│  GitHub Actions (CI/CD)                                  │
│  ├─ Runs tests                                          │
│  ├─ Builds Docker image                                 │
│  └─ Pushes to ECR                                       │
└────────────────┬────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────┐
│  AWS ECR (Image Registry)                                │
│  └─ Stores Docker image versions                        │
└────────────────┬────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────┐
│  AWS ECS (Container Orchestration)                       │
│  ├─ Pulls latest image                                  │
│  ├─ Stops old containers                                │
│  ├─ Starts new containers                               │
│  └─ Registers with load balancer                        │
└────────────────┬────────────────────────────────────────┘
                 │
        ┌────────┴──────────┐
        │                   │
        ▼                   ▼
┌──────────────┐    ┌──────────────┐
│  PostgreSQL  │    │  Redis       │
│  (Database)  │    │  (Cache)     │
└──────────────┘    └──────────────┘
        │
        ▼
┌──────────────────────────────────┐
│  AWS ALB (Load Balancer)          │
│  └─ api.geoni.ai traffic          │
└──────────────────────────────────┘
        │
        ▼
    USERS (Browser)
    ├─ Frontend from Vercel (geoni.ai)
    ├─ API from AWS (api.geoni.ai)
    └─ Emails from Resend
```

---

## 💡 **Real Example: You Make 1 Change**

**Scenario:** You fix a bug in crawler.py

```bash
# 1. Edit locally
nano crawler.py
# (fix bug)

# 2. Commit & push
git add crawler.py
git commit -m "Fix crawler URL parsing"
git push origin main
# ↓ (5 seconds)

# 3. GitHub Actions starts
# - Runs tests
# - Builds Docker image with new code
# - Pushes to ECR
# - Updates ECS service
# ↓ (2 minutes)

# 4. New code is LIVE
# Users automatically get the fixed version
# No manual deployment needed!

# 5. Monitor
aws logs tail /ecs/geoni-scanner --follow
# See the new version running
```

---

## 🎯 **Monthly Operations**

### **Daily (5 minutes)**
- Check CloudWatch dashboard
- Skim error logs
- Monitor audit success rate

### **Weekly (30 minutes)**
- Review performance metrics
- Check database size
- Analyze user feedback

### **Monthly (2 hours)**
- Plan optimizations
- Review cost analysis
- Backup verification
- Security audit

---

## 💰 **What Costs What (Monthly)**

| Service | Cost | Notes |
|---------|------|-------|
| **Vercel** | $0-20 | Free for hobby, $20 for Pro |
| **AWS RDS** | $15 | db.t3.micro + storage |
| **AWS Redis** | $15 | cache.t3.micro |
| **AWS ECS** | $30-50 | 2 Fargate tasks |
| **AWS ALB** | $16 | Load balancer |
| **AWS Data** | $50 | Estimated data transfer |
| **Resend** | $20 | 100 emails free, then $0.10/email |
| **GitHub** | Free | Private repos are free |
| **Total** | **~$150-170** | Scales with users |

---

## 🔑 **Quick Reference: Key Commands**

```bash
# GitHub - Push code
git push origin main

# AWS - Check service status
aws ecs describe-services --cluster geoni-cluster --services geoni-scanner-service

# AWS - View logs
aws logs tail /ecs/geoni-scanner --follow

# AWS - Restart service
aws ecs update-service --cluster geoni-cluster --service geoni-scanner-service --force-new-deployment

# Vercel - Deploy frontend
vercel --prod

# Email - Test sending
python -c "from email import send_welcome_email; send_welcome_email('test@example.com')"
```

---

## 🚨 **Troubleshooting Quick Fixes**

| Problem | Fix |
|---------|-----|
| API down | `aws ecs update-service --force-new-deployment` |
| Emails not sending | Check Resend API key in secrets |
| Frontend not loading | Check Vercel deployment logs |
| Database full | Scale RDS instance larger |
| High costs | Reduce ECS task count or downgrade instance |

---

## 📞 **Support Resources**

- **Vercel Docs:** https://vercel.com/docs
- **AWS Docs:** https://docs.aws.amazon.com/
- **Resend Docs:** https://resend.com/docs
- **GitHub Actions:** https://docs.github.com/en/actions

---

## ✅ **Final Checklist: Ready to Deploy?**

- [ ] All 17 backend files in geoni-scanner repo
- [ ] AWS CLI configured with credentials
- [ ] Docker installed locally
- [ ] aws-setup.sh script created
- [ ] GitHub repo created (private)
- [ ] GitHub secrets added (AWS keys)
- [ ] GitHub Actions workflow file (.github/workflows/deploy.yml)
- [ ] React frontend scaffolded
- [ ] Vercel account connected to GitHub
- [ ] Resend account created
- [ ] Environment variables set everywhere
- [ ] First push to main triggers GitHub Actions
- [ ] Docker image appears in ECR
- [ ] ECS service starts new tasks
- [ ] API responds at https://api.geoni.ai/health
- [ ] Frontend loads at https://geoni.vercel.app
- [ ] First audit completes end-to-end
- [ ] Email received with results

---

## 🎉 **You're Ready!**

At this point you have:
- ✅ Production backend on AWS
- ✅ Frontend on Vercel with custom domain
- ✅ Automated CI/CD (push code → live in 2 min)
- ✅ Email notifications via Resend
- ✅ Monitoring via CloudWatch
- ✅ Zero-downtime deployments
- ✅ Scalable infrastructure
- ✅ Cost-effective setup (~$150/month)

**Next:** Optimize, add features, grow the user base! 🚀
