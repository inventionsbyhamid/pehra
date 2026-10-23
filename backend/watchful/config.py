import math
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # each of these names a registered component (see watchful/registry.py and components.py)
    storage: str = "local"
    db: str = "sqlite"
    ai_provider: str = "openrouter"
    metrics: str = "log"
    notify: str = "log"
    detector: str = "yolox_nano"
    scan_detector: str = "yolox_nano_320"  # recordings: 45% faster, about 2 more people in 100 missed
    pose_model: str = "mediapipe"
    face_model: str = "yunet_sface"
    embedder: str = "siglip2_b32"

    data_dir: Path = Path("data")
    web_dist: Path | None = None  # the built web app; defaults to web/dist in the repo
    models_dir: Path = Path("models")
    analysis_fps: float = 5.0
    index_embeddings: bool = True  # the background indexer behind search
    display_fps: float = 10.0  # live view frames; analysis runs at analysis_fps underneath
    display_quality: int = 70
    analysis_width: int = 960
    tuning_file: Path | None = None
    timezone: str = "Asia/Kolkata"
    dnn_engine: Literal["auto", "new", "classic"] = "auto"
    night_mode: Literal["auto", "on", "off"] = "auto"

    demo_realtime: bool = True
    live_fps_during_scan: float = 2.0
    alert_dedupe_days: float = 14
    scan_workers: int = 0
    scan_pool_at_start: bool = True  # start and warm the scan workers with the app, not on the first upload
    camera_threads: int = 2  # OpenCV threads per live camera process
    scan_overlap_s: float = 2.0
    ai_parallel: int = 6

    openrouter_api_key: str | None = None
    openrouter_model: str = "deepseek/deepseek-v4.1-flash"
    # optional per task models, so compiling, checking and answering can use different models
    ai_model_compile: str | None = None
    ai_model_verify: str | None = None
    ai_model_ask: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    ai_max_tokens: int = 1500
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


def available_cpus(cpu_max: Path = Path("/sys/fs/cgroup/cpu.max")) -> int:
    """CPUs this process may use: a container's quota when there is one, else every core."""
    try:
        quota, period = cpu_max.read_text().split()[:2]
        if quota != "max":
            return max(1, math.floor(int(quota) / int(period)))
    except (OSError, ValueError):
        pass
    return os.cpu_count() or 1
