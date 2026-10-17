from __future__ import annotations

from types import SimpleNamespace

import pytest

from epaper_goodnews.config import AppConfig, OpenAIConfig, _build_storage
from epaper_goodnews.headline import HeadlineError, HeadlinePayload, generate_headline
from epaper_goodnews.models import Article, PositiveArticle


class FakeResponses:
    def __init__(self, response):
        self._response = response
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


def fake_client(parsed, *, status="completed", output=None):
    response = SimpleNamespace(
        id="resp_h",
        status=status,
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        output=output or [],
        output_parsed=parsed,
        usage=None,
    )
    return SimpleNamespace(responses=FakeResponses(response))


@pytest.fixture
def config(tmp_path):
    return AppConfig(
        openai=OpenAIConfig(api_key="test"),
        storage=_build_storage(tmp_path / "data"),
    )


@pytest.fixture
def positives():
    article = Article(guid="g", title="Otters return to the Liffey", summary="", link="")
    return [PositiveArticle(article=article, score=0.9, rationale="wildlife")]


def test_generates_headline_with_structured_output(config, positives):
    client = fake_client(HeadlinePayload(headline=" Otters home again ", subheading=" Sub. "))

    result = generate_headline(config=config, client=client, positives=positives)

    assert result.headline == "Otters home again"
    assert result.subheading == "Sub."
    assert result.response_id == "resp_h"
    call = client.responses.calls[0]
    assert call["text_format"] is HeadlinePayload
    assert call["reasoning"] == {"effort": "low"}
    assert call["max_output_tokens"] >= 1000
    assert "Otters return to the Liffey" in call["input"]


def test_blank_subheading_becomes_none(config, positives):
    client = fake_client(HeadlinePayload(headline="Hi", subheading="  "))
    assert generate_headline(config=config, client=client, positives=positives).subheading is None


def test_blank_headline_raises(config, positives):
    client = fake_client(HeadlinePayload(headline=" ", subheading="x"))
    with pytest.raises(HeadlineError, match="missing"):
        generate_headline(config=config, client=client, positives=positives)


def test_incomplete_raises(config, positives):
    with pytest.raises(HeadlineError, match="incomplete"):
        generate_headline(
            config=config, client=fake_client(None, status="incomplete"), positives=positives
        )


def test_refusal_raises(config, positives):
    refusal = SimpleNamespace(content=[SimpleNamespace(type="refusal", refusal="nope")])
    with pytest.raises(HeadlineError, match="refused"):
        generate_headline(
            config=config, client=fake_client(None, output=[refusal]), positives=positives
        )


def test_requires_positives(config):
    with pytest.raises(HeadlineError):
        generate_headline(config=config, client=fake_client(None), positives=[])
