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

    # IA
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    classification_model: str = "claude-haiku-4-5"
    transcription_model: str = "whisper-1"

    # App
    log_level: str = "INFO"


settings = Settings()  # type: ignore[call-arg]
