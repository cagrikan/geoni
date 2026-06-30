#!/bin/bash

# GEONI AWS Deployment Setup Script
# This script automates AWS infrastructure setup for GEONI backend

set -e

echo "╔════════════════════════════════════════════════════╗"
echo "║         GEONI AWS Deployment Setup                ║"
echo "╚════════════════════════════════════════════════════╝"
echo ""

# Configuration
AWS_REGION=${AWS_REGION:-"eu-central-1"}
AWS_ACCOUNT_ID=${AWS_ACCOUNT_ID:-""}
ECR_REPOSITORY="geoni-scanner"
RDS_INSTANCE="geoni-postgres"
REDIS_CLUSTER="geoni-redis"
ECS_CLUSTER="geoni-cluster"
ECS_SERVICE="geoni-scanner-service"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Helper functions
check_aws_cli() {
    if ! command -v aws &> /dev/null; then
        echo -e "${RED}❌ AWS CLI is not installed${NC}"
        echo "Install from: https://aws.amazon.com/cli/"
        exit 1
    fi
    echo -e "${GREEN}✅ AWS CLI found${NC}"
}

check_aws_credentials() {
    if ! aws sts get-caller-identity &> /dev/null; then
        echo -e "${RED}❌ AWS credentials not configured${NC}"
        echo "Run: aws configure"
        exit 1
    fi
    AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
    echo -e "${GREEN}✅ AWS credentials valid (Account: $AWS_ACCOUNT_ID)${NC}"
}

check_docker() {
    if ! command -v docker &> /dev/null; then
        echo -e "${RED}❌ Docker is not installed${NC}"
        exit 1
    fi
    echo -e "${GREEN}✅ Docker found${NC}"
}

create_ecr_repository() {
    echo ""
    echo "Creating ECR Repository..."
    
    if aws ecr describe-repositories --repository-names $ECR_REPOSITORY --region $AWS_REGION &> /dev/null; then
        echo -e "${YELLOW}⚠️  ECR repository already exists${NC}"
    else
        aws ecr create-repository \
            --repository-name $ECR_REPOSITORY \
            --region $AWS_REGION \
            --image-scanning-configuration scanOnPush=true
        echo -e "${GREEN}✅ ECR repository created${NC}"
    fi
}

create_rds_database() {
    echo ""
    echo "Creating RDS PostgreSQL Instance..."
    echo -e "${YELLOW}⚠️  This will take 3-5 minutes...${NC}"
    
    if aws rds describe-db-instances --db-instance-identifier $RDS_INSTANCE --region $AWS_REGION &> /dev/null 2>&1; then
        echo -e "${YELLOW}⚠️  RDS instance already exists${NC}"
    else
        # Generate random password
        DB_PASSWORD=$(openssl rand -base64 32)
        
        aws rds create-db-instance \
            --db-instance-identifier $RDS_INSTANCE \
            --db-instance-class db.t3.micro \
            --engine postgres \
            --engine-version 15.3 \
            --master-username geoni_admin \
            --master-user-password "$DB_PASSWORD" \
            --allocated-storage 20 \
            --storage-type gp3 \
            --no-publicly-accessible \
            --backup-retention-period 7 \
            --enable-cloudwatch-logs-exports postgresql \
            --region $AWS_REGION
        
        echo -e "${GREEN}✅ RDS instance creation initiated${NC}"
        echo -e "${YELLOW}⚠️  Save this password: $DB_PASSWORD${NC}"
        
        # Wait for database to be ready
        echo "Waiting for database to be available..."
        aws rds wait db-instance-available \
            --db-instance-identifier $RDS_INSTANCE \
            --region $AWS_REGION
        echo -e "${GREEN}✅ Database is ready${NC}"
    fi
}

create_redis_cluster() {
    echo ""
    echo "Creating ElastiCache Redis Cluster..."
    
    if aws elasticache describe-cache-clusters --cache-cluster-id $REDIS_CLUSTER --region $AWS_REGION &> /dev/null 2>&1; then
        echo -e "${YELLOW}⚠️  Redis cluster already exists${NC}"
    else
        aws elasticache create-cache-cluster \
            --cache-cluster-id $REDIS_CLUSTER \
            --cache-node-type cache.t3.micro \
            --engine redis \
            --engine-version 7.0 \
            --num-cache-nodes 1 \
            --automatic-failover-enabled \
            --region $AWS_REGION
        
        echo -e "${GREEN}✅ Redis cluster creation initiated${NC}"
        
        # Wait for cluster to be available
        echo "Waiting for Redis to be available..."
        aws elasticache wait cache-cluster-available \
            --cache-cluster-id $REDIS_CLUSTER \
            --region $AWS_REGION
        echo -e "${GREEN}✅ Redis is ready${NC}"
    fi
}

create_ecs_cluster() {
    echo ""
    echo "Creating ECS Cluster..."
    
    if aws ecs describe-clusters --clusters $ECS_CLUSTER --region $AWS_REGION | grep -q "clusterArn"; then
        echo -e "${YELLOW}⚠️  ECS cluster already exists${NC}"
    else
        aws ecs create-cluster \
            --cluster-name $ECS_CLUSTER \
            --region $AWS_REGION
        echo -e "${GREEN}✅ ECS cluster created${NC}"
    fi
}

create_cloudwatch_log_group() {
    echo ""
    echo "Creating CloudWatch Log Group..."
    
    LOG_GROUP="/ecs/$ECR_REPOSITORY"
    
    if aws logs describe-log-groups --log-group-name-prefix $LOG_GROUP --region $AWS_REGION | grep -q "logGroupName"; then
        echo -e "${YELLOW}⚠️  Log group already exists${NC}"
    else
        aws logs create-log-group \
            --log-group-name $LOG_GROUP \
            --region $AWS_REGION
        echo -e "${GREEN}✅ Log group created${NC}"
    fi
}

build_and_push_image() {
    echo ""
    echo "Building Docker Image..."
    
    # Get ECR registry URL
    ECR_REGISTRY=$(aws ecr describe-repositories --repository-names $ECR_REPOSITORY --region $AWS_REGION --query 'repositories[0].repositoryUri' --output text | cut -d'/' -f1)
    
    echo "ECR Registry: $ECR_REGISTRY"
    
    # Login to ECR
    echo "Logging into ECR..."
    aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ECR_REGISTRY
    
    # Build image
    docker build -t $ECR_REGISTRY/$ECR_REPOSITORY:latest .
    docker tag $ECR_REGISTRY/$ECR_REPOSITORY:latest $ECR_REGISTRY/$ECR_REPOSITORY:v1
    
    echo -e "${GREEN}✅ Docker image built${NC}"
    
    # Push image
    echo "Pushing image to ECR..."
    docker push $ECR_REGISTRY/$ECR_REPOSITORY:latest
    docker push $ECR_REGISTRY/$ECR_REPOSITORY:v1
    
    echo -e "${GREEN}✅ Image pushed to ECR${NC}"
}

setup_github_secrets() {
    echo ""
    echo "GitHub Secrets Setup (Manual)"
    echo ""
    echo "Add these secrets to your GitHub repository:"
    echo "  Settings > Secrets and variables > Actions > New repository secret"
    echo ""
    echo "Required secrets:"
    echo "  AWS_ACCESS_KEY_ID: <your-access-key>"
    echo "  AWS_SECRET_ACCESS_KEY: <your-secret-key>"
    echo "  SLACK_WEBHOOK: <optional-slack-webhook>"
    echo ""
}

print_summary() {
    echo ""
    echo "╔════════════════════════════════════════════════════╗"
    echo "║           Setup Complete! ✅                       ║"
    echo "╚════════════════════════════════════════════════════╝"
    echo ""
    echo "Next steps:"
    echo "1. Push code to GitHub:"
    echo "   git add ."
    echo "   git commit -m 'Initial deployment setup'"
    echo "   git push origin main"
    echo ""
    echo "2. Add GitHub Secrets (see above)"
    echo ""
    echo "3. Monitor deployment:"
    echo "   - GitHub Actions: github.com/YOUR_REPO/actions"
    echo "   - AWS Console: https://console.aws.amazon.com/ecs"
    echo "   - CloudWatch Logs: https://console.aws.amazon.com/logs"
    echo ""
    echo "4. Check backend health:"
    echo "   aws ecs describe-services --cluster $ECS_CLUSTER --services $ECS_SERVICE --region $AWS_REGION"
    echo ""
}

# Main execution
main() {
    echo "Checking prerequisites..."
    check_aws_cli
    check_aws_credentials
    check_docker
    
    echo ""
    echo "AWS Region: $AWS_REGION"
    echo "AWS Account: $AWS_ACCOUNT_ID"
    echo ""
    
    # Create infrastructure
    create_ecr_repository
    create_rds_database
    create_redis_cluster
    create_ecs_cluster
    create_cloudwatch_log_group
    
    # Build and push
    build_and_push_image
    
    # Print instructions
    setup_github_secrets
    print_summary
}

# Run main
main

echo "Script completed!"
