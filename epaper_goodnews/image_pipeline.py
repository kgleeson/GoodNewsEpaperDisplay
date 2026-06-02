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
    descriptors = "; ".join(p.article.title for p in positives if p.article.title)
    if not descriptors:
        descriptors = headline.headline

    return (
        f"Create a watercolour illustration that depicts the subject of a specific news story. "
        f"THE NEWS (this is what to illustrate — be literal and specific to it): {descriptors}. "
        f"Overall theme: {headline.headline}. "
        f"Pick the SINGLE most visually concrete subject from the news above and depict that one thing directly. "
        f"Show the actual subject of the story — the specific people, place, activity, object, or event it describes. "
        f"For example: an exam story shows students at desks writing; a renovation story shows a building being repaired; "
        f"a climate story shows the specific weather, energy, or nature element involved; a sports story shows that sport. "
        f"Do NOT default to generic scenery. Do NOT add rolling hills, cottages, castles, stone walls, or pastoral countryside "
        f"unless the news story is specifically about those things. The setting must come from the news, not from a generic template. "
        f"Do not include any text, lettering, captions, signs, or typography. "
        f"COMPOSITION: Fill the frame. Focus on the subject up close — a clear, readable focal point that fills most of the image. "
        f"Avoid wide empty landscapes; favour the people, object, or action that the story is actually about. "
        f"When characters appear: natural human proportions, expressive realistic faces, painted and integrated — not cartoon-styled. "
        f"STYLE: Rich painterly handmade watercolour. Loose wet washes, visible pigment blooms, wet-on-wet blending, "
        f"natural colour bleeding at edges. High-quality illustrated book aesthetic — expressive and human, not cartoon. "
        f"Thin organic dark-brown linework, loose and selective. "
        f"LIGHT AND COLOUR: Keep the image BRIGHT and well-lit overall — like clear daylight, not dusk or gloom. "
        f"Let the subject guide the palette; use whatever colours the story calls for, warm or cool. "
        f"Avoid an overall dark, heavy, or shadowy image — favour light, open, brightly-lit scenes. "
        f"DISPLAY CONSTRAINTS (7-colour e-paper, actual ink appearances): "
        f"the 'blue' ink renders PURPLE/VIOLET — avoid large pure-blue areas (skies, water); use teal/cyan instead (maps to green), "
        f"or keep sky minimal. The 'yellow' ink renders OLIVE — avoid yellow as a dominant tone. "
        f"Avoid yellow or blue clothing; use red, green, orange, brown, or white. "
        f"Use BRIGHT MID-TONES — dark shades of any colour collapse to near-black, so avoid large deeply-shadowed areas. "
        f"Keep whites clean and bright (any warm tint renders olive); keep foliage bright medium green, not dark. "
        f"Not a poster, not flat vector art, not cartoon illustration, no typography."
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
