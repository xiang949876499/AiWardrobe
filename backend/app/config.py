from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore")

    app_name: str = "AiWardrobe"
    environment: str = "development"
    testing: bool = False
    database_url: str = "postgresql+psycopg://aiwardrobe:aiwardrobe@localhost:5432/aiwardrobe"
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

    outfit_ai_provider: str = "deepseek"
    outfit_ai_base_url: str | None = None
    outfit_ai_api_key: str | None = None
    outfit_ai_model: str | None = None

    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_api_key: str | None = None
    deepseek_model: str = "deepseek-chat"

    weather_provider: str = "open_meteo"
    open_meteo_base_url: str = "https://api.open-meteo.com"
    solar_terms_api_url: str = "https://www.hko.gov.hk/en/gts/astronomy/data/files/24SolarTerms_{year}.xml"

    taobao_app_key: str | None = None
    taobao_app_secret: str | None = None
    taobao_adzone_id: str | None = None
    taobao_api_base_url: str = "https://eco.taobao.com/router/rest"
    shopping_recommendation_demo_mode: bool = True

    garment_ai_provider: str = "qwen"

    workflow_provider: str = "demo"

    comfyui_base_url: str = "http://127.0.0.1:8188"
    comfyui_client_id: str = "aiwardrobe-backend"
    comfyui_poll_interval_seconds: float = 1.0
    comfyui_poll_timeout_seconds: int = 180
    comfyui_garment_workflow_file: str = "workflows/garment_recognition.json"
    comfyui_load_image_node_id: str = "78"

    runninghub_base_url: str = "https://www.runninghub.cn"
    runninghub_api_key: str | None = None
    runninghub_poll_interval_seconds: float = 2.0
    runninghub_poll_timeout_seconds: int = 600
    runninghub_garment_workflow_file: str = "workflows/garment_recognition.json"
    runninghub_tryon_workflow_file: str = "workflows/ai_tryon.json"

    @model_validator(mode="after")
    def reject_sqlite_outside_tests(self) -> "Settings":
        if self.database_url.startswith("sqlite") and not self.testing:
            raise ValueError("SQLite is only allowed when TESTING=true; set DATABASE_URL to Postgres for app runtime")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
