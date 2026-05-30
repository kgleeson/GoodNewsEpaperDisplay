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
        f"Create a traditional children's picture-book watercolour illustration inspired by this theme: "
        f"'{headline.headline}'. Do not include any visible text, lettering, captions, slogans, signs, or typography. "
        f"Depict one gentle story moment rather than a poster or group portrait. "
        f"Use one clear focal character or focal action, placed centrally or slightly off-centre, "
        f"with a few smaller supporting characters, props, or symbolic background details arranged around it. "
        f"Use varied scale, overlap, and asymmetry. Avoid a row of people facing the viewer. "
        f"Visual style: handmade watercolour on warm cream paper, like a classic children's storybook page. "
        f"Use soft translucent paint washes, visible paper grain, pale blooms, uneven pigment, loose wet edges, "
        f"and gentle colour bleeding. Let the paint define some edges. "
        f"Linework: thin, loose, slightly broken hand-drawn contours in warm dark brown or muted charcoal. "
        f"The outlines should feel sketchy and imperfect, not bold or graphic. "
        f"Characters: rounded, friendly, simplified storybook people with expressive poses, small dot eyes, "
        f"simple curved noses, warm smiles, rosy cheeks, soft hair shapes, and softly shaded clothing. "
        f"Palette: warm, cheerful, and muted watercolour tones — terracotta orange, coral red, golden yellow, "
        f"sea green, muted teal, soft sky blue, and cream paper. Avoid harsh saturation and large flat colour blocks. "
        f"Background: light, airy, and suggestive, with loosely painted scenery such as sky, sea, grass, buildings, "
        f"landmarks, or symbolic objects. Keep the background secondary, softly washed, and lighter than the figures. "
        f"Final image should feel painterly, organic, warm, celebratory, and handmade — a single illustrated scene "
        f"from a children's book, not a poster, card, logo, comic panel, vector graphic, or clean digital cartoon. "
        f"Designed for a 7-colour ePaper display, so keep the main shapes readable with clear figure/background separation, "
        f"but preserve the soft watercolour texture. Avoid heavy black areas and muddy dark mid-tones. "
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
