"""
Endpoint santé. Utilisé par Coolify (healthcheck), monitoring externes
et tests rapides ("est-ce que le backend tourne ?").
"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/")
async def root():
    return {"service": "opslens-backend", "status": "ok"}
