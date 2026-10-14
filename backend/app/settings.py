from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    supabase_url: str = ''
    supabase_service_role_key: str = ''
    database_url: str = ''
    zoho_accounts_url: str = 'https://accounts.zoho.com'
    zoho_api_domain: str = 'https://www.zohoapis.com'
    openai_api_key: str = ''
    openai_model: str = 'gpt-4o-mini'
    app_timezone: str = 'Asia/Kolkata'
    zoho_refresh_cron_secret: str = ''
    cors_origins: str = 'http://localhost:5173,http://127.0.0.1:5173,http://localhost:5176,http://127.0.0.1:5176'
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

settings = Settings()
