from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "AiWardrobe"
    environment: str = "development"
    testing: bool = False
    database_url: str = "sqlite:///./aiwardrobe.db"
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 60 * 24 * 7
    email_code_minutes: int = 10

    frontend_origin: str = "http://localhost:5173"

    storage_driver: str = "local"
    local_upload_dir: str = "uploads"
    s3_endpoint_url: str | None = None
    s3_bucket: str = "aiwardrobe"
    s3_region: str = "auto"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    public_storage_base_url: str | None = None

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str = "no-reply@aiwardrobe.local"

    ai_base_url: str = "https://api.openai.com/v1"
    ai_api_key: str | None = None
    ai_model: str = "gpt-4o-mini"
    ai_demo_mode: bool = Field(default=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
