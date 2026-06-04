"""
Point d'entrée FastAPI du backend OpsLens.
Lancement local : `uvicorn app.main:app --reload`
Conteneur : voir Dockerfile (CMD uvicorn ...).
"""

import logging

from fastapi import FastAPI

from app.config import settings
from app.routes import backfill, health, webhook

# Logging basique — Coolify capture stdout/stderr
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(
    title="OpsLens API",
    version="0.1.0",
    description="Backend d'ingestion WhatsApp pour OpsLens (pilote ADS).",
)

app.include_router(health.router)
app.include_router(webhook.router)
app.include_router(backfill.router)
