from __future__ import annotations

import base64
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from epaper_goodnews.config import AppConfig, OpenAIConfig, _build_storage
from epaper_goodnews.image_pipeline import (
    ImageGenerationError,
    fit_to_display,
    generate_image_assets,
    validate_image_size,
)
from epaper_goodnews.models import Article, HeadlineResult, PositiveArticle


@pytest.mark.parametrize("size", ["1024x1024", "1536x1024", "1024x1536", "auto"])
@pytest.mark.parametrize("model", ["gpt-image-1", "gpt-image-1-mini", "gpt-image-1.5"])
def test_gpt_image_1_family_accepts_fixed_sizes(model, size):
    validate_image_size(model, size)


@pytest.mark.parametrize("size", ["1600x960", "1792x1024", "512x512"])
def test_gpt_image_1_family_rejects_other_sizes(size):
    with pytest.raises(ImageGenerationError, match="not supported by gpt-image-1"):
        validate_image_size("gpt-image-1", size)


@pytest.mark.parametrize(
    "size", ["1600x960", "1536x1024", "1024x1024", "auto", "3840x2160", "2400x800"]
)
@pytest.mark.parametrize("model", ["gpt-image-2", "gpt-image-2.5-sunburst", "gpt-image-2.5-flare"])
def test_gpt_image_2_accepts_custom_sizes(model, size):
    validate_image_size(model, size)


@pytest.mark.parametrize(
    ("size", "reason"),
    [
        ("1600x961", "multiples of 16"),
        ("4000x1600", "longest edge"),
        ("2512x800", "3:1"),
        ("800x480", "total pixels"),
        ("3840x2400", "total pixels"),
        ("big", "WIDTHxHEIGHT"),
    ],
)
def test_gpt_image_2_rejects_invalid_sizes(size, reason):
    with pytest.raises(ImageGenerationError, match=reason):
        validate_image_size("gpt-image-2.5-sunburst", size)


def _two_tone(width, height):
    """Left/right quarters red, middle green — lets us detect crop vs stretch."""
    image = Image.new("RGB", (width, height), (0, 255, 0))
    image.paste((255, 0, 0), (0, 0, width // 4, height))
    image.paste((255, 0, 0), (3 * width // 4, 0, width, height))
    return image


def test_fit_to_display_centre_crops_instead_of_stretching():
    # 1536x1024 (3:2) is wider-ratio-shorter than 5:3, so the top/bottom get cropped.
    source = Image.new("RGB", (1536, 1024), (0, 255, 0))
    source.paste((255, 0, 0), (0, 0, 1536, 50))  # red band only at the very top
    fitted = fit_to_display(source)
    assert fitted.size == (800, 480)
    assert fitted.getpixel((400, 0))[1] > 200  # band cropped away, not squashed in


def test_fit_to_display_exact_ratio_keeps_everything():
    fitted = fit_to_display(_two_tone(1600, 960))
    assert fitted.size == (800, 480)
    assert fitted.getpixel((10, 240))[0] > 200
    assert fitted.getpixel((790, 240))[0] > 200


def _b64_png(width, height):
    buffer = BytesIO()
    _two_tone(width, height).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


class FakeImages:
    def __init__(self, b64):
        self._b64 = b64
        self.calls: list[dict] = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(model_dump=lambda **_: {"output_tokens": 1234})
        return SimpleNamespace(id="img_1", data=[SimpleNamespace(b64_json=self._b64)], usage=usage)


@pytest.fixture
def config(tmp_path):
    return AppConfig(
        openai=OpenAIConfig(api_key="test"),
        storage=_build_storage(tmp_path / "data"),
    )


def _inputs():
    article = Article(guid="g", title="Otters return", summary="", link="")
    positives = [PositiveArticle(article=article, score=0.9)]
    headline = HeadlineResult(headline="Otters", subheading=None, prompt="")
    return headline, positives


def test_generate_image_assets_requests_configured_size_and_saves_800x480(config):
    images = FakeImages(_b64_png(1600, 960))
    client = SimpleNamespace(images=images)
    headline, positives = _inputs()

    result = generate_image_assets(
        config=config,
        storage=config.storage,
        client=client,
        headline=headline,
        positives=positives,
        run_id="abc",
    )

    call = images.calls[0]
    assert call["model"] == "gpt-image-2.5-sunburst"
    assert call["size"] == "1600x960"
    assert call["quality"] == "high"
    assert result.source_size == "1600x960"
    assert result.usage == {"output_tokens": 1234}
    with Image.open(result.image_path) as saved:
        assert saved.size == (800, 480)


def test_generate_image_assets_rejects_invalid_size_without_calling_api(config):
    config.openai.image_size = "1600x961"
    images = FakeImages(_b64_png(1600, 960))
    headline, positives = _inputs()
    with pytest.raises(ImageGenerationError):
        generate_image_assets(
            config=config,
            storage=config.storage,
            client=SimpleNamespace(images=images),
            headline=headline,
            positives=positives,
            run_id="abc",
        )
    assert images.calls == []
