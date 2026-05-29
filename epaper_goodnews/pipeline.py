from __future__ import annotations

import logging
import uuid
from datetime import datetime
from threading import Event
from typing import Optional

from openai import OpenAI

from .config import AppConfig
from .display_controller import DisplayController
from .headline import HeadlineError, generate_headline
from .image_pipeline import ImageGenerationError, generate_image_assets
from .models import GenerationMetadata, RunStatus
from .positivity import PositivityError, select_positive_articles
from .rss_service import fetch_articles
from .storage import StorageConfig, update_current, write_metadata

LOGGER = logging.getLogger(__name__)


class CancelledError(RuntimeError):
    pass


def _check_cancel(cancel_event: Optional[Event]) -> None:
    if cancel_event and cancel_event.is_set():
        raise CancelledError("Job cancelled")


def run_generation(
    *,
    config: AppConfig,
    client: OpenAI,
    storage: StorageConfig,
    display: DisplayController,
    cancel_event: Optional[Event] = None,
) -> GenerationMetadata:
    run_id = uuid.uuid4().hex[:8]
    started_at = datetime.now()
    metadata = GenerationMetadata(
        run_id=run_id,
        started_at=started_at,
        timezone=config.scheduler.timezone,
        text_model=config.openai.text_model,
        image_model=config.openai.image_model,
        device_type=config.device_type,
    )

    try:
        _check_cancel(cancel_event)
        articles = fetch_articles(config.rss_feed_url, limit=None, timezone=config.scheduler.timezone)
        metadata.articles = articles

        _check_cancel(cancel_event)
        positives = select_positive_articles(
            config=config,
            client=client,
            articles=articles,
            cancel_event=cancel_event,
        )
        metadata.positive_articles = positives

        _check_cancel(cancel_event)
        headline = generate_headline(
            config=config,
            client=client,
            positives=positives,
            cancel_event=cancel_event,
        )
        metadata.headline = headline

        _check_cancel(cancel_event)
        image = generate_image_assets(
            config=config,
            storage=storage,
            client=client,
            headline=headline,
            positives=positives,
            run_id=run_id,
            cancel_event=cancel_event,
        )
        metadata.image = image

        _check_cancel(cancel_event)
        display.display_image(image.image_path)

        metadata.completed_at = datetime.now()
        metadata.status = RunStatus.SUCCESS
        LOGGER.info("Generation pipeline succeeded")

    except CancelledError:
        metadata.completed_at = datetime.now()
        metadata.status = RunStatus.CANCELLED
        metadata.errors.append("Job cancelled")
        LOGGER.info("Generation run cancelled")
    except PositivityError as exc:
        metadata.completed_at = datetime.now()
        metadata.status = RunStatus.FAILED
        metadata.errors.append(str(exc))
        LOGGER.error("Positivity step failed: %s", exc)
    except HeadlineError as exc:
        metadata.completed_at = datetime.now()
        metadata.status = RunStatus.FAILED
        metadata.errors.append(str(exc))
        LOGGER.error("Headline generation failed: %s", exc)
    except ImageGenerationError as exc:
        metadata.completed_at = datetime.now()
        metadata.status = RunStatus.FAILED
        metadata.errors.append(str(exc))
        LOGGER.error("Image generation failed: %s", exc)
    except Exception as exc:  # pragma: no cover - broad catch for reliability
        metadata.completed_at = datetime.now()
        metadata.status = RunStatus.FAILED
        metadata.errors.append(str(exc))
        LOGGER.exception("Unexpected error during generation")

    meta_path = write_metadata(metadata, storage)
    if metadata.image:
        try:
            update_current(storage, metadata.image.image_path, meta_path)
        except Exception as exc:  # pragma: no cover - filesystem edge
            LOGGER.warning("Failed to update current pointers: %s", exc)

    return metadata
