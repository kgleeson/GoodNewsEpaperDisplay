from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

DEFAULT_TIMEZONE = "Europe/Dublin"
DEFAULT_SCHEDULE_HOUR = 8
DEFAULT_SCHEDULE_MINUTE = 0


@dataclass
class OpenAIConfig:
    api_key: Optional[str]
    text_model: str = "gpt-5-nano"
    image_model: str = "gpt-image-1-mini"
    image_size: str = "1536x1024"
    organization: Optional[str] = None


@dataclass
class StorageConfig:
    base_path: Path
    images_path: Path
    metadata_path: Path
    logs_path: Path
    current_image: Path
    current_metadata: Path


@dataclass
class SchedulerConfig:
    hour: int = DEFAULT_SCHEDULE_HOUR
    minute: int = DEFAULT_SCHEDULE_MINUTE
    timezone: str = DEFAULT_TIMEZONE


@dataclass
class WebConfig:
    host: str = "0.0.0.0"
    port: int = 8000


@dataclass
class AppConfig:
    openai: OpenAIConfig
    storage: StorageConfig
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    web: WebConfig = field(default_factory=WebConfig)
    rss_feed_url: str = "https://www.rte.ie/feeds/rss/?index=%2Fnews%2F"
    positivity_sample_size: int = 8
    positivity_select_count: int = 3


def _ensure_directories(storage: StorageConfig) -> None:
    storage.base_path.mkdir(parents=True, exist_ok=True)
    storage.images_path.mkdir(parents=True, exist_ok=True)
    storage.metadata_path.mkdir(parents=True, exist_ok=True)
    storage.logs_path.mkdir(parents=True, exist_ok=True)


def _build_storage(base_path: Path) -> StorageConfig:
    images = base_path / "images"
    metadata = base_path / "metadata"
    logs = base_path.parent / "logs"
    current_image = base_path / "current.png"
    current_metadata = base_path / "current.json"
    storage = StorageConfig(
        base_path=base_path,
        images_path=images,
        metadata_path=metadata,
        logs_path=logs,
        current_image=current_image,
        current_metadata=current_metadata,
    )
    _ensure_directories(storage)
    return storage


def load_config(env_path: Optional[Path] = None) -> AppConfig:
    if env_path is None:
        env_path = Path.cwd() / ".env"

    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv()

    data_dir = Path(os.getenv("EPAPER_DATA_DIR", Path.cwd() / "data"))
    openai_api_key = os.getenv("OPENAI_API_KEY")
    openai_org = os.getenv("OPENAI_ORG")

    scheduler_hour = int(os.getenv("EPAPER_SCHEDULE_HOUR", DEFAULT_SCHEDULE_HOUR))
    scheduler_minute = int(os.getenv("EPAPER_SCHEDULE_MINUTE", DEFAULT_SCHEDULE_MINUTE))
    timezone = os.getenv("EPAPER_TIMEZONE", DEFAULT_TIMEZONE)

    web_host = os.getenv("EPAPER_WEB_HOST", "0.0.0.0")
    web_port = int(os.getenv("EPAPER_WEB_PORT", 8000))

    sample_size = int(os.getenv("EPAPER_POSITIVITY_SAMPLE_SIZE", 8))
    select_count = int(os.getenv("EPAPER_POSITIVITY_SELECT_COUNT", 3))

    config = AppConfig(
        openai=OpenAIConfig(
            api_key=openai_api_key,
            organization=openai_org,
        ),
        storage=_build_storage(data_dir),
        scheduler=SchedulerConfig(
            hour=scheduler_hour,
            minute=scheduler_minute,
            timezone=timezone,
        ),
        web=WebConfig(host=web_host, port=web_port),
        rss_feed_url=os.getenv(
            "EPAPER_RSS_FEED_URL", "https://www.rte.ie/feeds/rss/?index=%2Fnews%2F"
        ),
        positivity_sample_size=sample_size,
        positivity_select_count=select_count,
    )

    return config
