#!/bin/bash
# GEONI scanner deploy — imaj BULUTTA derlenir (lokal Docker gerekmez/istenmez;
# Mac'te amd64 Playwright build'i disk yutuyor, kullanici karari 2026-07-14).
#
#   1) Kaynagi zip'le, S3'e koy
#   2) CodeBuild dene; hesap kotasi 0 ise tek seferlik EC2 build makinesi
#      (kendini terminate eder) ile ayni isi yap
#   3) API + worker servislerini yeni imajla yeniden baslat
#
# Kullanim: ./deploy.sh            # build + iki servisi de guncelle
set -euo pipefail
cd "$(dirname "$0")"

BUCKET=geoni-build-src-016031489497
ECR=016031489497.dkr.ecr.eu-central-1.amazonaws.com

echo "1/3 kaynak yukleniyor..."
TMP=$(mktemp -d); zip -qr "$TMP/scanner-src.zip" . -x "__pycache__/*" "*.pyc" ".env*"
aws s3 cp "$TMP/scanner-src.zip" "s3://$BUCKET/scanner-src.zip" --only-show-errors

echo "2/3 bulutta build..."
if BUILD_ID=$(aws codebuild start-build --project-name geoni-scanner-build \
      --query 'build.id' --output text 2>/dev/null); then
  echo "  CodeBuild: $BUILD_ID"
  while :; do
    ST=$(aws codebuild batch-get-builds --ids "$BUILD_ID" --query 'builds[0].buildStatus' --output text)
    [ "$ST" != "IN_PROGRESS" ] && break; sleep 20
  done
  [ "$ST" = "SUCCEEDED" ] || { echo "CodeBuild basarisiz: $ST"; exit 1; }
else
  echo "  CodeBuild kotasi kapali — tek seferlik EC2 build"
  AMI=$(aws ssm get-parameter --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 --query 'Parameter.Value' --output text)
  cat > "$TMP/userdata.sh" <<'EOF'
#!/bin/bash
set -x; exec > /var/log/geoni-build.log 2>&1
dnf install -y docker unzip; systemctl start docker
cd /tmp && aws s3 cp s3://geoni-build-src-016031489497/scanner-src.zip . && mkdir src && cd src && unzip -q ../scanner-src.zip
ECR=016031489497.dkr.ecr.eu-central-1.amazonaws.com
aws ecr get-login-password --region eu-central-1 | docker login --username AWS --password-stdin $ECR
docker build -t $ECR/geoni-scanner:latest . && docker push $ECR/geoni-scanner:latest
shutdown -h now
EOF
  IID=$(aws ec2 run-instances --image-id "$AMI" --instance-type t3.large \
    --iam-instance-profile Name=geoni-ec2-build \
    --instance-initiated-shutdown-behavior terminate \
    --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":30,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
    --subnet-id subnet-d1978e9c --associate-public-ip-address \
    --user-data "file://$TMP/userdata.sh" \
    --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=geoni-oneshot-build}]' \
    --query 'Instances[0].InstanceId' --output text)
  echo "  EC2: $IID (bitince kendini kapatir)"
  while [ "$(aws ec2 describe-instances --instance-ids "$IID" --query 'Reservations[0].Instances[0].State.Name' --output text)" != "terminated" ]; do sleep 30; done
fi

echo "3/3 servisler yeni imajla yeniden baslatiliyor..."
aws ecs update-service --cluster geoni-cluster --service geoni-scanner-service --force-new-deployment --query 'service.serviceName' --output text
aws ecs update-service --cluster geoni-cluster --service geoni-scan-worker --force-new-deployment --query 'service.serviceName' --output text
echo "Tamam. API sagligi: curl -s https://api.geoni.ai/health"
