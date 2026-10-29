# Deployment Guide

This document details how to deploy the Data Migration Validator locally on Windows/Linux or in containerized environments (Docker, AWS ECS/Fargate).

---

## 1. Local Windows Deployment (Recommended for Local Dev)

### Prerequisites
- Windows 11 / 10 (64-bit)
- Python 3.12+
- PostgreSQL 18.6+ running on `localhost:5432`
- AWS CLI v2 with configured profile (`personal-dev`)

### Quickstart Command
```powershell
.\scripts\run_local.ps1
```

---

## 2. Linux / macOS Deployment

```bash
# 1. Clone repository and navigate to root
cd data-migration-validator

# 2. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies and package
pip install -r requirements.txt
pip install -e .

# 4. Copy configuration template
cp .env.example .env
# Edit .env with your PostgreSQL credentials and AWS region

# 5. Initialize metadata schema
python scripts/initialize_metadata.py

# 6. Launch Streamlit server
streamlit run app.py --server.port 8501
```

---

## 3. Docker Deployment

### Dockerfile
```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy source and migrations
COPY src/ ./src/
COPY migrations/ ./migrations/
COPY pages/ ./pages/
COPY .streamlit/ ./.streamlit/
COPY app.py ./

# Install package
RUN pip install --no-cache-dir -e .

EXPOSE 8501

HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health || exit 1

ENTRYPOINT ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
```

### Build & Run
```bash
docker build -t data-migration-validator:1.0.0 .
docker run -p 8501:8501 --env-file .env data-migration-validator:1.0.0
```
