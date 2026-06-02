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


def _build_image_prompt(
    headline: HeadlineResult, positives: List[PositiveArticle]
) -> str:
    descriptors = ", ".join(p.article.title for p in positives if p.article.title)
    if not descriptors:
        descriptors = headline.headline

    return (
        f"Create a watercolour illustration for a 7-colour e-paper display. "
        f"Use the theme '{headline.headline}' only as inspiration. Do not include any visible text, lettering, captions, signs, or typography. "
        f"Composition: fill the entire frame edge-to-edge with rich painted colour. "
        f"One clear central subject or scene. Characters are welcome but not required — "
        f"if the story can be told through landscape, objects, animals, or symbolic imagery alone, that is preferred. "
        f"When characters appear, give them natural human proportions and expressive realistic faces — "
        f"not cartoon exaggeration, not oversized heads, not simplified dot eyes. "
        f"Characters should feel painted and integrated into the scene, not placed on top of it. "
        f"Style: rich, painterly handmade watercolour — loose wet washes, visible pigment blooms, "
        f"soft wet-on-wet blending, and natural colour bleeding at edges. "
        f"Warm, expressive, and human — like a high-quality illustrated book, not a children's cartoon. "
        f"Linework: thin warm dark-brown lines, loose and organic, used selectively. Not thick black outlines. "
        f"DISPLAY COLOUR CONSTRAINTS — this display's inks render as: "
        f"black (dark navy), white, forest green, purple-violet (the 'blue' ink), brick red, olive (the 'yellow' ink), copper orange. "
        f"Avoid pure blue sky — the blue ink is purple/violet. Use warm golden-hour sky, or teal/cyan (which maps to green). "
        f"Avoid yellow or blue clothing — use red, green, orange, or white instead. "
        f"Use BRIGHT MID-TONES — dark tones of any hue collapse to near-black on this display. No large deeply shadowed areas. "
        f"White areas must be clean bright white — any warm tint will render as olive-yellow. "
        f"Grass and foliage: bright medium green, not deep or dark green. "
        f"Palette: warm and rich — orange, brick red, bright green, copper, with white highlights. "
        f"Background: fully painted. Golden or warm sky, bright rolling hills, warm sunlight, or vivid landscape. "
        f"Not a poster, not vector art, not clipart, not a flat cartoon, and no typography. "
        f"Scene inspiration: {descriptors}."
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
            quality="high",
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
