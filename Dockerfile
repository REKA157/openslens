FROM python:3.12-slim

# Build marker — bump to invalidate Coolify cache
# v45: fusion vision→classification (messages à photo compris) + filet anomalie
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install Python deps. Statsmodels et numpy/pandas ont des wheels pré-compilées
# pour Python 3.12 sur Linux, donc pas besoin de build-essential ni gcc.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
