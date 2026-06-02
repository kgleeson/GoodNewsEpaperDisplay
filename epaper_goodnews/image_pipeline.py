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
        f"Create a watercolour illustration inspired by this Irish news story: '{headline.headline}'. "
        f"The image should evoke the specific story — its setting, action, people, or symbolic meaning. "
        f"Do not include any text, lettering, captions, signs, or typography. "
        f"COMPOSITION: Fill the entire frame with rich painted colour. "
        f"Choose the most visually compelling aspect of the story — characters in action, a meaningful landscape, "
        f"symbolic objects, animals, or a combination. "
        f"Characters are not required; if the story is better told through place, nature, or symbolic imagery, prefer that. "
        f"When characters appear: natural human proportions, expressive realistic faces, "
        f"painted and integrated into the scene — not cartoon-styled, not simplified. "
        f"STYLE: Rich painterly handmade watercolour. Loose wet washes, visible pigment blooms, "
        f"wet-on-wet blending, natural colour bleeding at edges. "
        f"High-quality illustrated book aesthetic — expressive and human, not cartoon. "
        f"Thin organic dark-brown linework, loose and selective. "
        f"COLOUR AND MOOD: Let the story guide the palette — it does not need to be warm or sunset-toned. "
        f"Use a balanced mix of warm and cool tones appropriate to the subject. "
        f"Greens, whites, earth tones, and muted teals all work well on this display. "
        f"DISPLAY CONSTRAINTS (7-colour e-paper — actual ink appearances): "
        f"black=dark navy, white=white, green=forest green, "
        f"'blue' ink=PURPLE/VIOLET (avoid large areas of pure blue), "
        f"'yellow' ink=OLIVE/YELLOW-GREEN (avoid yellow as a dominant tone), "
        f"'orange' ink=copper brown. "
        f"For sky: avoid pure blue (renders purple). "
        f"Good alternatives: overcast grey-green Irish sky, golden-hour or dawn sky, "
        f"teal or cyan sky (teal maps to green on display), or a composition where sky is minimal. "
        f"Clothing: avoid yellow or blue. Use red, green, orange, brown, or white. "
        f"Tones: use BRIGHT MID-TONES throughout — dark shades of any colour collapse to near-black. "
        f"No large deeply shadowed areas. Foliage: bright medium green, not dark. "
        f"White: keep clean and bright — any warm tint renders as olive-yellow. "
        f"Not a poster, not flat vector art, not cartoon illustration, no typography. "
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
