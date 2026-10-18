from __future__ import annotations

import base64
import logging
import re
from datetime import datetime
from io import BytesIO
from threading import Event

from openai import OpenAI
from PIL import Image, ImageOps
from PIL.Image import Resampling

from .config import AppConfig, StorageConfig
from .models import HeadlineResult, ImageResult, PositiveArticle
from .structured import usage_dict
from .style import DISPLAY_NOTES, REFERENCE_PREAMBLE, STYLE_PROMPT, reference_images

LOGGER = logging.getLogger(__name__)

DISPLAY_WIDTH = 800
DISPLAY_HEIGHT = 480

# gpt-image-1 family (gpt-image-1, -mini, 1.5) only accepts these fixed sizes.
GPT_IMAGE_1_SIZES = {"auto", "1024x1024", "1536x1024", "1024x1536"}

# gpt-image-2 and later accept custom WxH within these limits.
CUSTOM_SIZE_EDGE_MULTIPLE = 16
CUSTOM_SIZE_MAX_EDGE = 3840
CUSTOM_SIZE_MAX_RATIO = 3.0
CUSTOM_SIZE_MIN_PIXELS = 655_360
CUSTOM_SIZE_MAX_PIXELS = 8_294_400

_SIZE_RE = re.compile(r"^(\d+)x(\d+)$")


class ImageGenerationError(RuntimeError):
    pass


def _build_image_prompt(
    headline: HeadlineResult, positives: list[PositiveArticle], *, with_references: bool = False
) -> str:
    descriptors = "; ".join(p.article.title for p in positives if p.article.title)
    if not descriptors:
        descriptors = headline.headline

    scene = (
        f"SUBJECT: illustrate a specific news story. THE NEWS (be literal and specific to it): {descriptors}. "
        f"Overall theme: {headline.headline}. "
        f"Pick the SINGLE most visually concrete subject from the news above and depict that one thing directly — "
        f"the specific people, place, activity, object or event it describes. For example: an exam story shows "
        f"students at desks writing; a renovation story shows a building being repaired; a sports story shows that "
        f"sport. Do NOT default to generic scenery (rolling hills, cottages, castles, stone walls) unless the story "
        f"is about it. One main subject with at most two or three supporting elements. "
    )
    preamble = REFERENCE_PREAMBLE if with_references else ""
    return preamble + scene + STYLE_PROMPT + DISPLAY_NOTES


def validate_image_size(model: str, size: str) -> None:
    """Raise ImageGenerationError if ``size`` is not valid for ``model``."""
    if size == "auto":
        return
    if model.startswith("gpt-image-1"):
        if size not in GPT_IMAGE_1_SIZES:
            raise ImageGenerationError(
                f"Image size {size!r} is not supported by {model}; "
                f"use one of {sorted(GPT_IMAGE_1_SIZES)}"
            )
        return

    match = _SIZE_RE.match(size)
    if not match:
        raise ImageGenerationError(f"Image size {size!r} must be 'auto' or WIDTHxHEIGHT")
    width, height = int(match.group(1)), int(match.group(2))
    problems = []
    if width % CUSTOM_SIZE_EDGE_MULTIPLE or height % CUSTOM_SIZE_EDGE_MULTIPLE:
        problems.append(f"edges must be multiples of {CUSTOM_SIZE_EDGE_MULTIPLE}")
    if max(width, height) > CUSTOM_SIZE_MAX_EDGE:
        problems.append(f"longest edge must be <= {CUSTOM_SIZE_MAX_EDGE}")
    if min(width, height) == 0 or max(width, height) / min(width, height) > CUSTOM_SIZE_MAX_RATIO:
        problems.append("aspect ratio must be at most 3:1")
    if not CUSTOM_SIZE_MIN_PIXELS <= width * height <= CUSTOM_SIZE_MAX_PIXELS:
        problems.append(
            f"total pixels must be between {CUSTOM_SIZE_MIN_PIXELS:,} and {CUSTOM_SIZE_MAX_PIXELS:,}"
        )
    if problems:
        raise ImageGenerationError(
            f"Image size {size!r} is not valid for {model}: " + "; ".join(problems)
        )


def fit_to_display(image: Image.Image) -> Image.Image:
    """Centre-crop to the panel's aspect ratio and resize, without stretching."""
    return ImageOps.fit(
        image, (DISPLAY_WIDTH, DISPLAY_HEIGHT), method=Resampling.LANCZOS, centering=(0.5, 0.5)
    )


def _decode_base64_image(b64_data: str) -> Image.Image:
    raw = base64.b64decode(b64_data)
    return Image.open(BytesIO(raw)).convert("RGB")


def generate_image_assets(
    *,
    config: AppConfig,
    storage: StorageConfig,
    client: OpenAI,
    headline: HeadlineResult,
    positives: list[PositiveArticle],
    run_id: str,
    cancel_event: Event | None = None,
) -> ImageResult:
    if cancel_event and cancel_event.is_set():
        raise ImageGenerationError("Image generation cancelled")

    references = reference_images() if config.openai.image_style_references else []
    prompt = _build_image_prompt(headline, positives, with_references=bool(references))
    request_size = config.openai.image_size
    validate_image_size(config.openai.image_model, request_size)

    try:
        if references:
            # Style references go through the edit endpoint, which accepts several input
            # images and generates a new image guided by them.
            LOGGER.info("Requesting image with %d style references", len(references))
            response = client.images.edit(
                model=config.openai.image_model,
                image=[(path.name, path.read_bytes(), "image/png") for path in references],
                prompt=prompt,
                size=request_size,
                quality=config.openai.image_quality,  # type: ignore[arg-type]
                background="opaque",
            )
        else:
            LOGGER.info("Requesting image generation from OpenAI")
            response = client.images.generate(
                model=config.openai.image_model,
                prompt=prompt,
                size=request_size,
                quality=config.openai.image_quality,  # type: ignore[arg-type]
                background="opaque",
            )
    except Exception as exc:
        raise ImageGenerationError(f"OpenAI image generation failed: {exc}") from exc

    if cancel_event and cancel_event.is_set():
        raise ImageGenerationError("Image generation cancelled after response")

    try:
        if response.data is None:
            raise ImageGenerationError("OpenAI image response data is None")
        image_data = response.data[0].b64_json
    except (AttributeError, IndexError, TypeError) as exc:
        raise ImageGenerationError("Invalid OpenAI image response structure") from exc

    if not isinstance(image_data, str) or not image_data:
        raise ImageGenerationError("Image data is missing or not a valid string")

    usage = usage_dict(response)
    if usage:
        LOGGER.info("OpenAI image usage: %s", usage)

    base_image = _decode_base64_image(image_data)
    source_size = f"{base_image.width}x{base_image.height}"
    LOGGER.info("Received %s image (requested %s)", source_size, request_size)
    web_image = fit_to_display(base_image)

    timestamp = datetime.now().strftime("%Y-%m-%d")
    png_name = f"{timestamp}-{run_id}.png"
    png_path = storage.images_path / png_name
    web_image.save(png_path, format="PNG")

    LOGGER.info("Saved image to %s", png_path)

    return ImageResult(
        image_path=png_path,
        prompt=prompt,
        response_id=getattr(response, "id", None),
        width=DISPLAY_WIDTH,
        height=DISPLAY_HEIGHT,
        requested_size=request_size,
        source_size=source_size,
        usage=usage,
    )
