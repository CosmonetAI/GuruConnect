from functools import lru_cache
import ipaddress
from pathlib import Path
import re
from typing import Literal
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from psycopg.conninfo import conninfo_to_dict
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False, hide_input_in_errors=True)
    environment: Literal["development", "local", "test", "staging", "production"] = "development"
    local_data_dir: Path = Path(__file__).resolve().parents[1] / ".local-data"
    database_url: SecretStr | None = None
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: SecretStr | None = None
    jwt_audience: str = "authenticated"
    jwt_issuer: str = ""
    require_admin_mfa: bool = False
    credential_encryption_key: SecretStr | None = None
    credential_key_version: str = "1"
    cors_origins: list[str] = ["http://localhost:5176", "http://127.0.0.1:5176", "http://localhost:5174", "http://127.0.0.1:5174"]
    trusted_hosts: list[str] = ["localhost", "127.0.0.1", "::1", "testserver"]
    request_body_timeout_seconds: float = Field(default=15, gt=0, le=120)
    max_concurrent_requests: int = Field(default=20, ge=1, le=1000)
    preauth_rate_limit_per_minute: int = Field(default=300, ge=1, le=10000)
    preauth_rate_limit_max_peers: int = Field(default=4096, ge=1, le=100000)
    storage_bucket: str = "session-audio"
    pool_min_size: int = Field(default=1, ge=1, le=10)
    pool_max_size: int = Field(default=10, ge=1, le=50)
    upload_max_bytes: int = Field(default=6_000_000, ge=32044, le=10_000_000)
    recording_max_seconds: int = Field(default=7200, ge=30, le=7200)
    window_seconds: int = Field(default=30, ge=5, le=120)
    context_seconds: int = Field(default=2, ge=0, le=10)
    signed_url_seconds: int = Field(default=120, ge=30, le=600)
    worker_lease_seconds: int = Field(default=180, ge=30, le=900)
    rate_limit_per_minute: int = Field(default=120, ge=10, le=10000)
    elevenlabs_api_key: SecretStr | None = None
    azure_translator_key: SecretStr | None = None
    azure_translator_region: str = ""
    azure_translator_endpoint: str = "https://api.cognitive.microsofttranslator.com"

    @field_validator("environment", mode="before")
    @classmethod
    def normalize_environment(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("supabase_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if value:
            parsed = cls.safe_url(value)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                    or parsed.query or parsed.fragment or parsed.path not in {"", "/"} or any(c.isspace() for c in value)):
                raise ValueError("SUPABASE_URL must be an HTTPS origin without credentials, path, query or fragment")
            cls.validate_hosts([parsed.hostname])
            if parsed.port not in {None, 443}:
                raise ValueError("SUPABASE_URL must use the standard HTTPS port")
        return value.rstrip("/")

    @field_validator("cors_origins")
    @classmethod
    def validate_cors(cls, value: list[str]) -> list[str]:
        for origin in value:
            parsed = cls.safe_url(origin)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
                    or parsed.path or parsed.query or parsed.fragment or "*" in origin or any(c.isspace() for c in origin)):
                raise ValueError("CORS_ORIGINS must contain exact HTTP(S) origins without paths or credentials")
            cls.validate_hosts([parsed.hostname])
        return list(dict.fromkeys(value))

    @staticmethod
    def safe_url(value):
        try:
            parsed = urlsplit(value)
            parsed.port
            return parsed
        except ValueError:
            raise ValueError("Invalid URL format") from None

    @field_validator("supabase_service_role_key", "supabase_anon_key")
    @classmethod
    def validate_service_keys(cls, value):
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        if raw and (len(raw) > 16384 or any(ord(char) < 33 or ord(char) > 126 for char in raw)):
            raise ValueError("Supabase credentials must be printable tokens without whitespace")
        return value

    @field_validator("trusted_hosts")
    @classmethod
    def validate_hosts(cls, value: list[str]) -> list[str]:
        for host in value:
            if not host or host != host.lower() or "*" in host or host.endswith("."):
                raise ValueError("TRUSTED_HOSTS must contain exact lowercase hostnames or IP addresses")
            try:
                ipaddress.ip_address(host)
            except ValueError:
                if len(host) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in host.split(".")):
                    raise ValueError("TRUSTED_HOSTS must contain hostnames without schemes, paths or ports")
        if not value:
            raise ValueError("At least one TRUSTED_HOSTS entry is required")
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def validate_security_configuration(self):
        if self.pool_min_size > self.pool_max_size:
            raise ValueError("POOL_MIN_SIZE cannot exceed POOL_MAX_SIZE")
        if self.jwt_issuer and self.jwt_issuer != f"{self.supabase_url}/auth/v1":
            raise ValueError("JWT_ISSUER must match SUPABASE_URL/auth/v1")
        if not self.jwt_audience.strip():
            raise ValueError("JWT_AUDIENCE cannot be empty")
        if self.credential_encryption_key:
            try:
                Fernet(self.credential_encryption_key.get_secret_value().encode())
            except (ValueError, TypeError):
                raise ValueError("CREDENTIAL_ENCRYPTION_KEY must be a valid Fernet key") from None
        if self.deployed:
            if not self.configured or not self.credential_encryption_key:
                raise ValueError("Staging/production requires database, Supabase service credentials and credential encryption")
            for origin in self.cors_origins:
                parsed = urlsplit(origin)
                if parsed.scheme != "https" or self.local_host(parsed.hostname):
                    raise ValueError("Staging/production CORS_ORIGINS must use nonlocal HTTPS origins")
            if not self.cors_origins or any(self.local_host(host) for host in self.trusted_hosts):
                raise ValueError("Staging/production requires explicit nonlocal CORS_ORIGINS and TRUSTED_HOSTS")
            try:
                parameters = conninfo_to_dict(self.database_url.get_secret_value())
            except Exception:
                raise ValueError("DATABASE_URL must be a valid PostgreSQL connection string") from None
            if parameters.get("sslmode") != "verify-full":
                raise ValueError("Staging/production DATABASE_URL requires sslmode=verify-full")
        return self

    @staticmethod
    def local_host(host: str | None) -> bool:
        if host in {None, "localhost", "testserver"} or host.endswith(".localhost"):
            return True
        try:
            address = ipaddress.ip_address(host)
            return not address.is_global
        except ValueError:
            return False

    @property
    def deployed(self) -> bool:
        return self.environment in {"staging", "production"}

    @property
    def issuer(self) -> str:
        return self.jwt_issuer or f"{self.supabase_url}/auth/v1"

    @property
    def configured(self) -> bool:
        return bool(self.database_url and self.database_url.get_secret_value() and self.supabase_url
                    and self.supabase_service_role_key and self.supabase_service_role_key.get_secret_value()
                    and "YOUR_PROJECT" not in self.supabase_url)

    @property
    def has_cloud_configuration(self) -> bool:
        return bool(self.database_url or self.supabase_url or self.supabase_service_role_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
