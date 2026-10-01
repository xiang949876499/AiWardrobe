from functools import lru_cache
from decimal import Decimal

from pydantic import Field, model_validator, field_validator
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
    # Wedding assets must never share a bucket that may expose wardrobe images publicly.
    wedding_s3_bucket: str = "aiwardrobe-wedding-private"
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

    wedding_media_mode: str = "disabled"
    wedding_story_provider: str = "disabled"
    wedding_story_api_key: str | None = None
    wedding_story_model: str = "doubao-seed-2-0-pro-260215"
    wedding_frame_provider: str = "disabled"
    wedding_frame_api_key: str | None = None
    wedding_frame_model: str = "doubao-seedream-4-5-251128"
    wedding_character_video_provider: str = "disabled"
    wedding_character_video_api_key: str | None = None
    wedding_character_video_model: str = "wan2.7-r2v-2026-06-12"
    wedding_scene_video_provider: str = "disabled"
    wedding_scene_video_api_key: str | None = None
    wedding_scene_video_model: str = "wan2.6-i2v-flash"
    wedding_ark_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    wedding_dashscope_base_url: str = "https://dashscope.aliyuncs.com"
    wedding_connect_timeout_seconds: float = Field(default=10, gt=0)
    wedding_read_timeout_seconds: float = Field(default=60, gt=0)
    wedding_poll_interval_seconds: float = Field(default=3, gt=0)
    wedding_total_timeout_seconds: float = Field(default=600, gt=0)
    wedding_io_heartbeat_seconds: float = Field(default=0.25, gt=0, le=1)
    wedding_max_json_bytes: int = Field(default=32_000_000, gt=0)
    wedding_max_request_bytes: int = Field(default=60_000_000, gt=0)
    wedding_max_image_bytes: int = Field(default=20_000_000, gt=0)
    wedding_max_video_bytes: int = Field(default=100_000_000, gt=0)
    wedding_download_allowed_domains: str = "oss-cn-beijing.aliyuncs.com,oss-cn-shanghai.aliyuncs.com,oss-accelerate.aliyuncs.com,volces.com"
    wedding_price_version: str = "cn-baseline-2026-10-01"
    wedding_story_input_fen_per_million_tokens: Decimal | None = Field(default=None, gt=0, le=100_000_000)
    wedding_story_output_fen_per_million_tokens: Decimal | None = Field(default=None, gt=0, le=100_000_000)
    wedding_story_max_cost_fen: int = Field(default=100, gt=0)
    wedding_story_max_tokens: int = Field(default=8192, gt=0, le=16384)
    wedding_frame_fen_per_image: Decimal = Field(default=Decimal("25"), gt=0, le=1_000_000)
    wedding_r2v_fen_per_second: Decimal = Field(default=Decimal("100"), gt=0, le=1_000_000)
    wedding_i2v_flash_fen_per_second: Decimal = Field(default=Decimal("25"), gt=0, le=1_000_000)
    wedding_account_budget_limit_fen: int = Field(default=15_000, gt=0)
    wedding_worker_stale_seconds: int = Field(default=1_800, gt=0)
    wedding_ffmpeg_path: str | None = None
    wedding_ffprobe_path: str | None = None
    wedding_render_timeout_seconds: int = Field(default=900, ge=30, le=1800)
    wedding_render_threads: int = Field(default=2, ge=1, le=4)
    wedding_render_lease_seconds: int = Field(default=120, ge=10, le=3600)
    wedding_render_max_attempts: int = Field(default=3, ge=1, le=5)
    wedding_preview_bandwidth_bytes: int = Field(default=20 * 1024 ** 3, ge=0)
    wedding_subtitle_font: str = "Microsoft YaHei"
    wedding_s3_connect_timeout_seconds: int = Field(default=5, ge=1, le=30)
    wedding_s3_read_timeout_seconds: int = Field(default=30, ge=1, le=60)
    wedding_music_catalog_file: str | None = None
    wedding_music_file: str | None = None
    wedding_music_license_id: str | None = None

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

    @field_validator("wedding_media_mode", mode="before")
    @classmethod
    def normalize_wedding_mode(cls, value):
        value = str(value).strip().lower()
        if value not in {"disabled", "demo", "live"}:
            raise ValueError("WEDDING_MEDIA_MODE must be disabled, demo, or live")
        return value

    @model_validator(mode="after")
    def reject_sqlite_in_production(self) -> "Settings":
        if self.database_url.startswith("sqlite") and self.environment == "production" and not self.testing:
            raise ValueError("SQLite is only allowed for development or tests; set DATABASE_URL to Postgres in production")
        if self.storage_driver.strip().lower() == "s3" and self.s3_bucket == self.wedding_s3_bucket:
            raise ValueError("Wedding S3 assets require a bucket separate from wardrobe storage")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
