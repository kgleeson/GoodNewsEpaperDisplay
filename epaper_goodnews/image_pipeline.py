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
        f"Whimsical children's book watercolour illustration — loose hand-drawn outlines in dark brown or "
        f"muted charcoal (not thick black), soft visible paper grain, translucent paint washes, gentle colour "
        f"bleeding at edges, and an imperfect handmade quality throughout. "
        f"STYLE: traditional watercolour picture-book page, not digital art. Think hand-painted storybook. "
        f"Avoid: glossy digital art, vector art, thick black outlines, flat fills, hard edges, 3D rendering, "
        f"anime, photorealism, or clean cartoon style. "
        f"CHARACTERS: rounded and expressive with slightly oversized heads, simplified features, rosy cheeks, "
        f"and warm smiles. Skin and clothing have soft watercolour shading with visible brush texture and "
        f"uneven paint edges — no flat fills, no hard outlines. "
        f"PALETTE: warm and muted — soft terracotta orange, dusty red, golden yellow, teal blue, sea green, "
        f"and cream paper tones showing through washes. Colours are luminous but not harshly saturated. "
        f"BACKGROUND: atmospheric and suggestive — soft watercolour sky, sea, and grass washes that bleed "
        f"and blend loosely. Cream or warm white paper shows through in lighter areas. Keep backgrounds "
        f"significantly lighter than figures so characters read clearly against them. "
        f"COMPOSITION: one or two focal characters in the centre or foreground, with 1–3 supporting figures "
        f"or scene elements at smaller scale — avoid an equal-weight lineup across the frame. "
        f"Single cohesive scene with no panels, no text, no UI elements. "
        f"TECHNICAL: the image renders on a 7-colour ePaper display (white, black, red, green, blue, yellow, "
        f"orange) with dithering — the soft watercolour style works well here, but keep figure-to-background "
        f"contrast clear and avoid very dark mid-tones that dither into muddy blobs. "
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
