FROM python:3.12-slim

# Build marker — bump to invalidate Coolify cache
# v32: Phase 1 prédictif — Prophet forecast (build essentials added for Stan)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Deps système pour Prophet (Stan/cmdstanpy)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (better layer caching).
# Prophet install est lourd (~10 min) car cmdstanpy télécharge Stan.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
