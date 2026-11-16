from __future__ import annotations

import logging
import uuid
from datetime import datetime
from threading import Event
from zoneinfo import ZoneInfo

from openai import OpenAI

from .config import AppConfig
from .display_controller import DisplayController, RefreshCancelledError
from .headline import HeadlineError, generate_headline
from .image_pipeline import ImageGenerationError, generate_image_assets, validate_image_size
from .models import GenerationMetadata, RunStatus
from .positivity import PositivityError, select_positive_articles
from .rss_service import fetch_articles
from .storage import StorageConfig, update_current, write_metadata

LOGGER = logging.getLogger(__name__)


class CancelledError(RuntimeError):
    pass


def _now(timezone: str) -> datetime:
    """Wall-clock time in the schedule's zone, whatever the system timezone is.

    Naive on purpose: it is the format existing metadata uses, and the scheduler's
    catch-up check reads naive run times as being in the schedule's zone.
    """
    return datetime.now(ZoneInfo(timezone)).replace(tzinfo=None)


def _check_cancel(cancel_event: Event | None) -> None:
    if cancel_event and cancel_event.is_set():
        raise CancelledError("Job cancelled")


def _redisplay_current(
    storage: StorageConfig, display: DisplayController, cancel_event: Event | None
) -> None:
    """Refresh the panel with the existing current image (no-good-news days).

    Waveshare recommends refreshing the panel at least every 24 hours, so the
    previous image is pushed again rather than leaving the panel untouched.
    """
    if not storage.current_image.exists():
        LOGGER.warning("No current image to re-display; panel left unchanged")
        return
    display.display_image(storage.current_image, cancel_event=cancel_event)


_STAGE_LABELS = {
    PositivityError: "Positivity step failed",
    HeadlineError: "Headline generation failed",
    ImageGenerationError: "Image generation failed",
}


def _mark_cancelled(metadata: GenerationMetadata) -> None:
    metadata.status = RunStatus.CANCELLED
    metadata.errors.append("Job cancelled")
    LOGGER.info("Generation run cancelled")


def run_generation(
    *,
    config: AppConfig,
    client: OpenAI,
    storage: StorageConfig,
    display: DisplayController,
    cancel_event: Event | None = None,
) -> GenerationMetadata:
    run_id = uuid.uuid4().hex[:8]
    timezone = config.scheduler.timezone
    started_at = _now(timezone)
    metadata = GenerationMetadata(
        run_id=run_id,
        started_at=started_at,
        timezone=timezone,
        text_model=config.openai.text_model,
        image_model=config.openai.image_model,
        device_type=config.device_type,
        positivity_min_score=config.positivity_min_score,
    )

    try:
        # Fail fast on a misconfigured image size before spending any tokens.
        validate_image_size(config.openai.image_model, config.openai.image_size)

        _check_cancel(cancel_event)
        articles = fetch_articles(config.rss_feed_url, limit=None, timezone=timezone)
        metadata.articles = articles

        _check_cancel(cancel_event)
        selection = select_positive_articles(
            config=config,
            client=client,
            articles=articles,
            cancel_event=cancel_event,
        )
        metadata.article_scores = selection.scores
        positives = selection.selected
        metadata.positive_articles = positives

        if not positives:
            LOGGER.info(
                "No article scored >= %.2f; skipping generation and keeping current image",
                config.positivity_min_score,
            )
            metadata.status = RunStatus.SKIPPED
            metadata.errors.append(
                f"No article scored >= {config.positivity_min_score:.2f}; kept previous image"
            )
            try:
                _redisplay_current(storage, display, cancel_event)
            except RefreshCancelledError:
                raise
            except Exception as exc:
                metadata.status = RunStatus.FAILED
                metadata.errors.append(f"Re-display of current image failed: {exc}")
                LOGGER.error("Re-display of current image failed: %s", exc)
            metadata.completed_at = _now(timezone)
            write_metadata(metadata, storage)
            return metadata

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
            started_at=started_at,
        )
        metadata.image = image

        _check_cancel(cancel_event)
        display.display_image(image.image_path, cancel_event=cancel_event)

        metadata.status = RunStatus.SUCCESS
        LOGGER.info("Generation pipeline succeeded")

    except (CancelledError, RefreshCancelledError):
        _mark_cancelled(metadata)
    except (PositivityError, HeadlineError, ImageGenerationError) as exc:
        # A stage that notices the cancel flag raises its own error type.
        if cancel_event and cancel_event.is_set():
            _mark_cancelled(metadata)
        else:
            metadata.status = RunStatus.FAILED
            metadata.errors.append(str(exc))
            LOGGER.error("%s: %s", _STAGE_LABELS.get(type(exc), "Run failed"), exc)
    except Exception as exc:  # pragma: no cover - broad catch for reliability
        metadata.status = RunStatus.FAILED
        metadata.errors.append(str(exc))
        LOGGER.exception("Unexpected error during generation")

    metadata.completed_at = _now(timezone)
    meta_path = write_metadata(metadata, storage)
    # Only a fully successful run (image generated AND displayed) becomes "current".
    if metadata.status == RunStatus.SUCCESS and metadata.image:
        try:
            update_current(storage, metadata.image.image_path, meta_path)
        except Exception as exc:  # pragma: no cover - filesystem edge
            LOGGER.warning("Failed to update current pointers: %s", exc)

    return metadata
