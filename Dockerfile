FROM python:3.12-slim

# Build marker — bump to invalidate Coolify cache
# v36: install cmdstan binaries for Prophet (fix stan_backend AttributeError)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Deps système pour Prophet (Stan/cmdstanpy)
# - build-essential, gcc, g++ : compilation Stan
# - curl : téléchargement CmdStan
# - tbb : runtime Intel Threading Building Blocks (requis par CmdStan)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    curl \
    libtbb-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (better layer caching).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Compile CmdStan (binaires Stan que Prophet utilise). Étape lourde (~8-12 min)
# mais nécessaire — sans elle Prophet rejette toutes les requêtes avec
# AttributeError: 'Prophet' object has no attribute 'stan_backend'.
RUN python -c "import cmdstanpy; cmdstanpy.install_cmdstan(progress=False)"

# Copy app code
COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
