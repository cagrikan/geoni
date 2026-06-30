# GEONI Backend - Quick Start (Revised)
## For: github.com/cagrikan/geoni/geoni-scanner

---

## 📋 Right Now (Today)

### 1. Copy Files to geoni-scanner folder
```bash
cd ~/path/to/github/geoni/geoni-scanner
# Copy all 17 backend files here
```

### 2. Push to GitHub
```bash
git add .
git commit -m "GEONI Scanner Backend"
git branch -M main
git push -u origin main
```

### 3. Add GitHub Secrets
GitHub → Settings → Secrets → Add:
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`

### 4. Test Locally (5 min)
```bash
docker-compose up -d
curl http://localhost:8000/health
```

✅ **Result:** Backend running locally + pushed to GitHub

---

## 🏢 Tomorrow (AWS Setup - 30 min)

### 1. Configure AWS CLI
```bash
aws configure
# Enter: Access Key, Secret, Region (eu-central-1)
```

### 2. Run Auto-Setup
```bash
chmod +x aws-setup.sh
./aws-setup.sh
```

**Creates automatically:**
- ECR repository
- RDS PostgreSQL database
- Redis cache
- ECS cluster
- CloudWatch logs
- Builds & pushes Docker image

### 3. Add `.github/workflows/deploy-scanner.yml`

Copy the workflow file to enable:
- Auto-build on `git push`
- Auto-deploy to AWS

✅ **Result:** Full AWS infrastructure + CI/CD pipeline ready

---

## 🎨 Day 3 (Frontend - 30 min)

### 1. Create React App
```bash
cd ~/path/to/github/geoni
npm create vite@latest geoni-frontend -- --template react-ts
cd geoni-frontend
npm install axios react-router-dom zustand
```

### 2. Create `.env.production`
```
VITE_API_URL=https://api.geoni.ai
```

### 3. Push to GitHub
```bash
cd ..
git add geoni-frontend/
git commit -m "Add GEONI Frontend"
git push origin main
```

### 4. Deploy to Vercel
```bash
cd geoni-frontend
npm i -g vercel
vercel
# Set env var: VITE_API_URL = https://api.geoni.ai
```

✅ **Result:** Frontend live at `https://app.geoni.ai`

---

## 📧 Day 4 (Email - 20 min)

### 1. Create Resend Account
https://resend.com → Sign up → Get API key

### 2. Add GitHub Secret
```
RESEND_API_KEY = re_xxxxx
```

### 3. Add Email Code to Backend
See `DEPLOYMENT_GUIDE.md` section on email

### 4. Deploy
```bash
git add .
git commit -m "Add email"
git push origin main
# Auto-deployed in 2 min
```

✅ **Result:** Audit results sent via email

---

## ✅ Day 5 (Testing & Launch)

### 1. Test API
```bash
curl https://api.geoni.ai/health
curl https://api.geoni.ai/docs
```

### 2. Test Frontend
```bash
open https://app.geoni.ai
```

### 3. Full Test
- Enter domain: `example.com`
- Enter email: `test@example.com`
- Run audit (wait 3-5 min)
- Check email for results

### 4. Show ARD Team
- "Hey, audit your domain: https://geoni.ai"
- Collect feedback

✅ **Result:** PRODUCTION LIVE 🚀

---

## 🔄 Normal Workflow (After Launch)

### Push Code
```bash
cd geoni-scanner
# Make changes
git push origin main
# → Auto-deploys in 2 minutes
```

### Monitor
```bash
# Health check
curl https://api.geoni.ai/health

# See logs
aws logs tail /ecs/geoni-scanner --follow

# Check status
aws ecs describe-services --cluster geoni-cluster --services geoni-scanner-service
```

### Scale (If needed)
```bash
# More tasks
aws ecs update-service --cluster geoni-cluster --service geoni-scanner-service --desired-count 4

# Bigger database
aws rds modify-db-instance --db-instance-identifier geoni-postgres --db-instance-class db.t3.small
```

---

## 💰 Cost

- **AWS:** ~$130/month (RDS, Redis, ECS, ALB, data)
- **Vercel:** $20/month (frontend)
- **Resend:** ~$20/month (emails)
- **GitHub:** Free
- **Total:** ~$170/month

---

## 🚨 If Something Breaks

| Problem | Fix |
|---------|-----|
| API down | `aws ecs update-service --force-new-deployment` |
| GitHub Actions fail | Check AWS secrets |
| Database error | Wait 2 min, check password |
| Email not sending | Check `RESEND_API_KEY` in secrets |
| High cost | Reduce ECS task count |

---

## 📞 Detailed Docs

- **SETUP_REVISED.md** ← Full technical guide
- **DEPLOYMENT_GUIDE.md** ← Deep dive on each service
- **README.md** ← API documentation
- **DEVELOPMENT_SUMMARY.md** ← Architecture overview

---

## 🎯 Timeline

```
Today (Day 1)      → Push backend to GitHub + GitHub secrets
Tomorrow (Day 2)   → AWS setup (30 min)
Day 3              → Frontend creation + Vercel (30 min)
Day 4              → Email integration (20 min)
Day 5              → Testing + ARD demo
```

**Total time invested:** ~3 hours (spread over 5 days)  
**Result:** Production-grade platform with CI/CD, monitoring, email, auto-scaling

---

## ✅ Checklist - Right Now

- [ ] Files in `geoni-scanner` folder
- [ ] `git push origin main`
- [ ] GitHub secrets added (AWS keys)
- [ ] Local test: `docker-compose up -d` ✅

**That's it for today. Tomorrow: `./aws-setup.sh`**

---

**Questions? Read SETUP_REVISED.md (it has answers for everything)**

🚀
