from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr

class Settings(BaseSettings):
    supabase_url: str = ''
    supabase_service_role_key: str = ''
    database_url: str = ''
    zoho_accounts_url: str = 'https://accounts.zoho.com'
    zoho_api_domain: str = 'https://www.zohoapis.com'
    openai_api_key: str = ''
    openai_model: str = 'gpt-4o-mini'
    # Use the established sales model unless a deployment selects a dedicated
    # extraction model. An unconfigured model must not silently break live AI.
    voice_extraction_model: str = ''
    voice_extraction_webhook_secret: str = ''
    # Reuses OPENAI_API_KEY and the existing database; no inference service.
    catalogue_semantics_enabled: bool = True
    catalogue_embedding_model: str = 'text-embedding-3-small'
    catalogue_embedding_timeout_seconds: float = Field(default=20, ge=2, le=60)
    catalogue_refresh_seconds: int = Field(default=900, ge=60, le=86400)
    catalogue_sync_lease_seconds: int = Field(default=120, ge=60, le=600)
    app_timezone: str = 'Asia/Kolkata'
    zoho_refresh_cron_secret: str = ''
    smtp2go_api_key: SecretStr | None = None
    smtp2go_sender_email: str = ''
    smtp2go_sender_name: str = Field(default='GuruConnect', max_length=160)
    smtp2go_timeout_seconds: float = Field(default=20, ge=2, le=60)
    cors_origins: str = 'http://localhost:5173,http://127.0.0.1:5173,http://localhost:5176,http://127.0.0.1:5176'
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

settings = Settings()
