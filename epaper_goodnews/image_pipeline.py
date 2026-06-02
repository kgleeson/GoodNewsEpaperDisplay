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
        f"Create a children's picture-book watercolour illustration for a 7-colour e-paper display. "
        f"Use the theme '{headline.headline}' only as inspiration. Do not include any visible text, lettering, captions, signs, or typography. "
        f"Composition: fill the entire frame edge-to-edge with rich painted colour — no large empty areas, no bare white space. "
        f"One clear central subject or action, large and bold. Use 2–3 supporting characters or symbolic elements. "
        f"Avoid crowd scenes, tiny faces, thin props, and distant small details. "
        f"CRITICAL — this display has these actual ink colours: black (renders dark navy), white, forest green, "
        f"purple-violet (the 'blue' ink), brick red, olive yellow-green (the 'yellow' ink), and copper orange. "
        f"Design around this reality: "
        f"DO NOT USE BLUE SKY or blue water — the blue ink is purple/violet and will look wrong for sky. "
        f"Instead: use a warm sunrise or golden-hour sky in orange, yellow, and red tones. "
        f"Or use a bright green landscape that fills the upper background. "
        f"Characters should NOT wear blue clothing — use red, orange, green, or white instead. "
        f"Avoid any large blue areas as a design choice. "
        f"Use BRIGHT MID-TONES of every colour — dark tones of any hue collapse to near-black on this display. "
        f"No dark backgrounds, dark shadows, or deeply shaded fills. Keep all colours in the bright-to-mid range. "
        f"White clothing and white elements should be clearly white with minimal colour shading. "
        f"Style: bold, luminous handmade watercolour, children's storybook. "
        f"Colours are saturated and vibrant — not pale or washed out, and not dark. "
        f"Use broad energetic washes with visible pigment and painterly wet edges. "
        f"Linework: warm dark-brown hand-drawn outlines, slightly loose and imperfect but continuous. "
        f"Characters: rounded friendly storybook people, oversized heads, simple dot eyes, curved smiles, rosy cheeks, "
        f"clear hand gestures, simple clothing shapes. Faces large and readable. "
        f"Palette: lead with warm tones — strong orange, brick red, golden yellow, bright green. "
        f"Use hue contrast (warm vs cool) rather than dark/light contrast. "
        f"Background: fully painted and warm. Use golden sky, warm rolling hills, bright green meadows, "
        f"sunshine, or cheerful warm-coloured buildings. "
        f"Final image: a bold, joyful, warm-toned watercolour storybook scene that works within the display's "
        f"actual ink palette (no reliance on blue, warm skies, bright mid-tones). "
        f"Not a poster, not vector art, not clipart, not a clean digital cartoon, and no typography. "
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
