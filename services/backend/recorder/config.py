from functools import lru_cache
from pathlib import Path
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)
    environment: str = "development"
    local_data_dir: Path = Path(__file__).resolve().parents[1] / ".local-data"
    database_url: SecretStr | None = None
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: SecretStr | None = None
    jwt_audience: str = "authenticated"
    jwt_issuer: str = ""
    credential_encryption_key: SecretStr | None = None
    credential_key_version: str = "1"
    cors_origins: list[str] = ["http://localhost:5176", "http://127.0.0.1:5176", "http://localhost:5174", "http://127.0.0.1:5174"]
    storage_bucket: str = "recorder-audio"
    pool_min_size: int = Field(default=1, ge=1, le=10)
    pool_max_size: int = Field(default=10, ge=1, le=50)
    upload_max_bytes: int = Field(default=400_000, ge=32044, le=2_000_000)
    recording_max_seconds: int = Field(default=7200, ge=30, le=14400)
    window_seconds: int = Field(default=30, ge=5, le=120)
    context_seconds: int = Field(default=2, ge=0, le=10)
    signed_url_seconds: int = Field(default=120, ge=30, le=600)
    worker_lease_seconds: int = Field(default=180, ge=30, le=900)
    rate_limit_per_minute: int = Field(default=120, ge=10, le=10000)
    elevenlabs_api_key: SecretStr | None = None
    azure_translator_key: SecretStr | None = None
    azure_translator_region: str = ""
    azure_translator_endpoint: str = "https://api.cognitive.microsofttranslator.com"

    @field_validator("supabase_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if value and not value.startswith("https://"):
            raise ValueError("SUPABASE_URL must use HTTPS")
        return value.rstrip("/")

    @field_validator("cors_origins")
    @classmethod
    def validate_cors(cls, value: list[str]) -> list[str]:
        if "*" in value:
            raise ValueError("Explicit CORS origins are required")
        return value

    @property
    def issuer(self) -> str:
        return self.jwt_issuer or f"{self.supabase_url}/auth/v1"

    @property
    def configured(self) -> bool:
        return bool(self.database_url and self.database_url.get_secret_value() and self.supabase_url
                    and self.supabase_service_role_key and self.supabase_service_role_key.get_secret_value()
                    and "YOUR_PROJECT" not in self.supabase_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()
