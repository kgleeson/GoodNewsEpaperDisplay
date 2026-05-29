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

EPAPER_COLORS = [
    (255, 255, 255),  # White
    (0, 0, 0),  # Black
    (255, 0, 0),  # Red
    (0, 128, 0),  # Green
    (0, 0, 255),  # Blue
    (255, 255, 0),  # Yellow
    (255, 128, 0),  # Orange
]

VALID_IMAGE_SIZES = {
    "256x256",
    "512x512",
    "1024x1024",
    "1024x1536",
    "1536x1024",
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
        f"Create a lively watercolour comic-style illustration suited for a 7-colour ePaper display. "
        f"Emphasise painterly washes, soft edges, and layered brush strokes within the limited palette: "
        f"white, black, red, green, blue, yellow, and orange. Use playful composition and gentle gradients "
        f"while avoiding photorealism and hard geometric shapes. Frame the artwork as a single cohesive scene "
        f"(no panels or divided sections) and do not include any text. Incorporate themes from the headline "
        f"'{headline.headline}'. Inspiration: {descriptors}."
    )


def _decode_base64_image(b64_data: str) -> Image.Image:
    raw = base64.b64decode(b64_data)
    return Image.open(BytesIO(raw)).convert("RGB")


def _quantize_to_palette(image: Image.Image) -> Image.Image:
    palette_img = Image.new("P", (len(EPAPER_COLORS), 1))
    palette: list[int] = []
    for color in EPAPER_COLORS:
        palette.extend(color)
    padding = 768 - len(palette)
    if padding > 0:
        palette.extend([0] * padding)
    palette_img.putpalette(palette)
    return image.convert("RGB").quantize(
        palette=palette_img, dither=Image.Dither.FLOYDSTEINBERG
    )


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
    allowed_sizes = [
        "auto",
        "1024x1024",
        "1536x1024",
        "1024x1536",
        "256x256",
        "512x512",
        "1792x1024",
        "1024x1792",
    ]
    if request_size not in allowed_sizes:
        LOGGER.warning(
            "Requested image size %s is unsupported. Falling back to 1024x1024.",
            request_size,
        )
        request_size = "1024x1024"

    try:
        response = client.images.generate(
            model=config.openai.image_model,
            prompt=prompt,
            size=request_size,  # type: ignore
        )
    except Exception as exc:  # pragma: no cover
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
    web_image = base_image.resize((800, 480), Resampling.LANCZOS)
    display_image = _quantize_to_palette(web_image)

    timestamp = datetime.now().strftime("%Y-%m-%d")
    png_name = f"{timestamp}-{run_id}.png"
    bmp_name = f"{timestamp}-{run_id}.bmp"

    png_path = storage.images_path / png_name
    bmp_path = storage.images_path / bmp_name

    web_image.save(png_path, format="PNG")
    display_image.save(bmp_path, format="BMP")

    LOGGER.info("Saved image assets to %s and %s", png_path, bmp_path)

    return ImageResult(
        image_path=png_path,
        display_path=bmp_path,
        prompt=prompt,
        response_id=getattr(response, "id", None),
        width=800,
        height=480,
    )
