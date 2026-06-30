#!/bin/bash

# GEONI Visibility Scanner - Local Development Startup Script

set -e

echo "=========================================="
echo "GEONI Visibility Scanner - Local Startup"
echo "=========================================="

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker."
    exit 1
fi

# Check if .env file exists
if [ ! -f .env ]; then
    echo "⚠️  .env file not found. Copying from .env.example..."
    cp .env.example .env
    echo "✅ .env created. Update it with your values if needed."
fi

echo ""
echo "🚀 Starting services with Docker Compose..."
docker-compose up -d

echo ""
echo "⏳ Waiting for services to be healthy..."
sleep 5

# Check PostgreSQL
echo -n "Checking PostgreSQL... "
if docker-compose exec -T postgres pg_isready -U geoni_user > /dev/null 2>&1; then
    echo "✅"
else
    echo "❌"
    docker-compose logs postgres
    exit 1
fi

# Check Redis
echo -n "Checking Redis... "
if docker-compose exec -T redis redis-cli ping > /dev/null 2>&1; then
    echo "✅"
else
    echo "❌"
    docker-compose logs redis
    exit 1
fi

# Check FastAPI
echo -n "Checking FastAPI... "
sleep 3  # Give FastAPI time to start
if curl -s http://localhost:8000/health > /dev/null; then
    echo "✅"
else
    echo "❌"
    docker-compose logs backend
    exit 1
fi

echo ""
echo "=========================================="
echo "✅ All services running!"
echo "=========================================="
echo ""
echo "📚 API Documentation:"
echo "   Swagger UI: http://localhost:8000/docs"
echo "   ReDoc:      http://localhost:8000/redoc"
echo ""
echo "📊 Database:"
echo "   Host:     localhost:5432"
echo "   User:     geoni_user"
echo "   Password: geoni_password"
echo "   Database: geoni_scanner"
echo ""
echo "💾 Redis:"
echo "   URL: redis://localhost:6379"
echo ""
echo "🛑 To stop: docker-compose down"
echo "📜 Logs:    docker-compose logs -f backend"
echo ""
