from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    storage: Literal["local", "s3"] = "local"
    db: Literal["sqlite", "dynamodb"] = "sqlite"
    ai_provider: Literal["openrouter", "bedrock"] = "openrouter"
    metrics: Literal["log", "cloudwatch"] = "log"
    notify: Literal["log", "sns"] = "log"

    data_dir: Path = Path("data")
    models_dir: Path = Path("models")
    analysis_fps: float = 5.0
    analysis_width: int = 960
    tuning_file: Path | None = None
    timezone: str = "Asia/Kolkata"
    detector: Literal["yolox_tiny", "yolox_s"] = "yolox_tiny"
    dnn_engine: Literal["auto", "new", "classic"] = "auto"
    night_mode: Literal["auto", "on", "off"] = "auto"
    embedder: Literal["embeddinggemma2", "siglip2"] = "siglip2"

    scan_workers: int = 0
    scan_overlap_s: float = 1.0
    ai_parallel: int = 6

    openrouter_api_key: str | None = None
    openrouter_model: str = "deepseek/deepseek-v4.1-flash"
    bedrock_model: str | None = None
    aws_region: str = "us-east-1"
    s3_bucket: str | None = None
    ddb_table_prefix: str = "watchful"
    sns_topic_arn: str | None = None

    demo_user: str = "judge"
    demo_password: str
    session_secret: str

    ai_daily_call_limit: int = 500
    retention_days: int = 7
    max_upload_mb: int = 200
    max_upload_minutes: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()
