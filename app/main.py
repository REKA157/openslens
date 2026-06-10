"""
Point d'entrée FastAPI du backend OpsLens.
Lancement local : `uvicorn app.main:app --reload`
Conteneur : voir Dockerfile (CMD uvicorn ...).
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routes import backfill, dashboard, health, reports, sites, webhook

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

# CORS pour que le frontend (localhost, Vercel...) puisse appeler /api/*
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # à restreindre en prod (Vercel + localhost)
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(webhook.router)
app.include_router(backfill.router)
app.include_router(dashboard.router)
app.include_router(reports.admin_router)
app.include_router(reports.api_router)
app.include_router(sites.admin_router)
app.include_router(sites.api_router)
