"""
Configuration centrale du backend OpsLens.
Toutes les valeurs sont chargées depuis les variables d'environnement
(en local : fichier .env, en prod : variables Coolify).
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Supabase
    supabase_url: str
    supabase_secret_key: str

    # WAHA
    waha_base_url: str
    waha_api_key: str
    waha_session_name: str = "default"
    waha_webhook_secret: str | None = None

    # Pilote
    pilot_group_id: str
    company_id: str

    # URL que WAHA doit appeler pour livrer les events (webhook entrant).
    # Backend et WAHA tournent sur le même VPS Coolify ; le backend est exposé
    # sur l'IP publique:8001. Surchargeable via WEBHOOK_CALLBACK_URL si l'URL
    # interne change.
    webhook_callback_url: str = "http://2.24.15.60:8001/ingest/webhook/waha"

    # IA
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    classification_model: str = "claude-haiku-4-5"
    transcription_model: str = "whisper-1"

    # App
    log_level: str = "INFO"


settings = Settings()  # type: ignore[call-arg]
