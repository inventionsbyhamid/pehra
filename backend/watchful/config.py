import json
import math
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# a list setting may be written "a,b" or as JSON
Names = Annotated[list[str], NoDecode]

DATA_KEY_MIN = 32  # characters; store/sql.py derives the Fernet key from it with SHA 256
MAKE_SECRET = 'python -c "import secrets; print(secrets.token_hex(32))"'


def check_data_key(value: str, name: str = "DATA_KEY") -> str:
    """A key that seals camera addresses (NVR passwords): a blank or short one is refused rather than used."""
    if len(value.strip()) < DATA_KEY_MIN:
        what = "blank" if not value.strip() else f"only {len(value.strip())} characters"
        raise ValueError(f"{name} is {what}. It encrypts camera addresses and must be at least {DATA_KEY_MIN} random "
                         f"characters; make one with {MAKE_SECRET}")
    return value.strip()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # each of these names a registered component (see watchful/registry.py and components.py)
    storage: str = "local"
    db: str = "sql"  # SQLAlchemy; DATABASE_URL's scheme picks SQLite or PostgreSQL
    ai_provider: str = "openrouter"
    metrics: str = "log"
    notify: str = "log"
    detector: str = "yolox_nano"
    scan_detector: str = "yolox_nano_320"  # recordings: 45% faster, about 2 more people in 100 missed
    pose_model: str = "mediapipe"
    face_model: str = "yunet_sface"
    embedder: str = "siglip2_b32"
    ocr: str = "ppocr_v3det_v5rec"  # Zoo PP-OCRv3 detection, PP-OCRv5 recognition; ppocr_v3_crnn stays selectable
    plate_detector: str = "lpd_yunet"  # "none" reads plates from PP-OCR text boxes only

    data_dir: Path = Path("data")
    database_url: str | None = None  # default sqlite:///{data_dir}/watchful.db, see `database`
    data_key: str  # encrypts camera addresses at rest (store/sql.py UrlBox); required, at least DATA_KEY_MIN chars
    # earlier keys, which still open what they sealed until `python -m watchful.db rotate-keys` seals it all again
    # with DATA_KEY; only then can they be dropped
    data_key_old: Names = []
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

    # alerts: the channel incidents go to (alerts/telegram.py); without TELEGRAM_BOT_TOKEN the log channel stands in
    alert_channel: str = "telegram"
    telegram_bot_token: str | None = None  # from BotFather; never logged
    telegram_webhook_secret: str | None = None  # Telegram sends it back with every update; A-Z a-z 0-9 _ -, up to 256
    telegram_api_url: str = "https://api.telegram.org"
    run_jobs_in_app: bool = True  # false where systemd runs the job worker on its own (AWS)

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
    sns_topic_arn: str | None = None

    public_base_url: str = "http://localhost:8000"
    # Cloudflare's CF-Connecting-IP names the visitor only where every request comes through Cloudflare (the ioflood
    # tunnel); elsewhere a visitor could send it themselves (ratelimit.client_ip)
    trust_cloudflare: bool = False
    session_secret: str
    auth_providers: Names = ["google"]  # sign in providers offered, in order (auth/providers.py, auth/google.py)
    google_client_id: str | None = None
    google_client_secret: str | None = None
    # billing (billing/): Polar, or the fake provider when Polar's keys are missing (it takes payments only on http
    # localhost); POLAR_PRODUCTS is JSON naming the Polar product of each plan and period, {"society_month": ...}
    billing: str = "polar"
    polar_access_token: str | None = None
    polar_webhook_secret: str | None = None
    polar_server: Literal["sandbox", "production"] = "sandbox"
    polar_products: Annotated[dict[str, str], NoDecode] = {}

    ai_daily_call_limit: int = 500  # AI requests a day for a plan whose limit is agreed per customer (plans.json)
    retention_days: int = 7
    max_upload_mb: int = 200
    max_upload_minutes: int = 10
    # cameras the server pulls may be on private addresses only when the server itself runs inside the society's
    # network; on a cloud server that would let a camera address reach the server's own services (netguard.py)
    allow_private_streams: bool = False

    @field_validator("auth_providers", "data_key_old", mode="before")
    @classmethod
    def _names(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return json.loads(value) if value.startswith("[") else [v.strip() for v in value.split(",") if v.strip()]
        return value

    @field_validator("polar_products", mode="before")
    @classmethod
    def _products(cls, value):
        if isinstance(value, str):
            return json.loads(value) if value.strip() else {}
        return value

    @field_validator("data_key")
    @classmethod
    def _data_key(cls, value: str) -> str:
        return check_data_key(value)

    @field_validator("session_secret")
    @classmethod
    def _session_secret(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(f"SESSION_SECRET is blank. It signs sign in sessions and links; make one with "
                             f"{MAKE_SECRET}")
        return value

    @field_validator("data_key_old")
    @classmethod
    def _data_keys_old(cls, value: list[str]) -> list[str]:
        return [check_data_key(k, "DATA_KEY_OLD") for k in value]

    @field_validator("telegram_webhook_secret")
    @classmethod
    def _webhook_secret(cls, value: str | None) -> str | None:
        """Telegram's secret_token takes 1 to 256 of A to Z, a to z, 0 to 9, _ and -; setWebhook refuses others."""
        if not value:
            return None  # blank: no webhook
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value):
            raise ValueError("TELEGRAM_WEBHOOK_SECRET may hold only letters, digits, _ and - (at most 256); make one "
                             f"with {MAKE_SECRET}")
        return value

    @property
    def database(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir / 'watchful.db'}"


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
