"""
Client Supabase singleton.
Utilise la clé `service_role` côté backend pour bypasser RLS — toutes les
écritures de l'ingestion passent par ici. Le frontend, lui, utilisera la
publishable key avec RLS active.
"""

from functools import lru_cache
from supabase import create_client, Client

from app.config import settings


@lru_cache(maxsize=1)
def get_supabase() -> Client:
    return create_client(settings.supabase_url, settings.supabase_secret_key)
