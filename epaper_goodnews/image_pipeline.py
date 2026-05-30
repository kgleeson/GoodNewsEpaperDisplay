from __future__ import annotations

import base64
import logging
from datetime import datetime
from io import BytesIO
from threading import Event
from typing import List, Optional

from openai import OpenAI
from PIL import Image
from PIL.Image import Resampling

from .config import AppConfig, StorageConfig
from .models import HeadlineResult, ImageResult, PositiveArticle

LOGGER = logging.getLogger(__name__)

DISPLAY_WIDTH = 800
DISPLAY_HEIGHT = 480

ALLOWED_IMAGE_SIZES = {
    "auto",
    "1024x1024",
    "1536x1024",
    "1024x1536",
    "1792x1024",
    "1024x1792",
    "256x256",
    "512x512",
}


class ImageGenerationError(RuntimeError):
    pass


def _build_image_prompt(headline: HeadlineResult, positives: List[PositiveArticle]) -> str:
    descriptors = ", ".join(p.article.title for p in positives if p.article.title)
    if not descriptors:
        descriptors = headline.headline
    return (
        f"Create a warm, joyful watercolour-and-ink illustration in the style of a quality children's picture book. "
        f"STYLE: ink line-art outlines (not too thick) filled with loose, textured watercolour washes — "
        f"visible brushwork and paper texture throughout, including on characters' skin and clothing. "
        f"Characters are simplified but not flat: slightly stylized proportions (heads a touch larger than "
        f"realistic, simplified hands and features), expressive faces with rosy cheeks and joyful expressions. "
        f"The style sits between 'illustrated children's book' and 'watercolour sketch' — "
        f"NOT flat sticker art, NOT photorealistic. "
        f"COLOURS: warm and vibrant — terracotta oranges, brick reds, sky blues, leaf greens, warm yellows — "
        f"applied as visible watercolour washes with subtle colour variation within each area. "
        f"Clothing should be bright (red, orange, blue, green) with subtle watercolour texture and visible "
        f"brush edges — light shading only, not heavy modelling. "
        f"BACKGROUND: loose, painterly washes of sky and landscape — light and airy with cream paper showing through. "
        f"Keep backgrounds significantly lighter than the foreground figures. "
        f"Avoid photorealism, fine gradients between dark tones, very dark clothing or backgrounds. "
        f"The overall mood should be celebratory and uplifting. "
        f"COMPOSITION: one or two clear focal characters in the centre or foreground, with 1–3 supporting "
        f"figures or scene elements arranged around them at smaller scale — avoid an equal-weight lineup of "
        f"figures across the frame. Single cohesive landscape scene with no panels, no text, no UI elements. "
        f"TECHNICAL: the image renders on a 7-colour ePaper display (white, black, red, green, blue, yellow, "
        f"orange) with dithering — keep contrast high between light backgrounds and bright figures; "
        f"avoid dark mid-tones as they dither into muddy blobs. "
        f"Incorporate themes from the headline '{headline.headline}'. "
        f"Inspiration: {descriptors}."
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
    positives: List[PositiveArticle],
    run_id: str,
    cancel_event: Optional[Event] = None,
) -> ImageResult:
    if cancel_event and cancel_event.is_set():
        raise ImageGenerationError("Image generation cancelled")

    prompt = _build_image_prompt(headline, positives)
    LOGGER.info("Requesting image generation from OpenAI")

    request_size = config.openai.image_size
    if request_size not in ALLOWED_IMAGE_SIZES:
        LOGGER.warning(
            "Image size %s unsupported; falling back to 1536x1024", request_size
        )
        request_size = "1536x1024"

    try:
        response = client.images.generate(
            model=config.openai.image_model,
            prompt=prompt,
            size=request_size,  # type: ignore
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

    base_image = _decode_base64_image(image_data)
    web_image = base_image.resize((DISPLAY_WIDTH, DISPLAY_HEIGHT), Resampling.LANCZOS)

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
    )
