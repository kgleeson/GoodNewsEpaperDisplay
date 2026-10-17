from __future__ import annotations

from types import SimpleNamespace

import pytest

from epaper_goodnews.config import AppConfig, OpenAIConfig, _build_storage
from epaper_goodnews.models import Article
from epaper_goodnews.positivity import (
    PositivityError,
    PositivityScores,
    ScoredItem,
    select_positive_articles,
)


class FakeResponses:
    def __init__(self, response):
        self._response = response
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def fake_client(response):
    return SimpleNamespace(responses=FakeResponses(response))


def parsed_response(parsed, *, status="completed", output=None):
    return SimpleNamespace(
        id="resp_1",
        status=status,
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        output=output or [],
        output_parsed=parsed,
        usage=None,
    )


def scores(*pairs):
    return PositivityScores(
        items=[ScoredItem(id=i, positivity=p, rationale=f"r{i}") for i, p in pairs]
    )


@pytest.fixture
def config(tmp_path):
    return AppConfig(
        openai=OpenAIConfig(api_key="test"),
        storage=_build_storage(tmp_path / "data"),
    )


@pytest.fixture
def articles():
    return [
        Article(guid=f"https://rte.ie/news/{i}", title=f"Story {i}", summary=f"S{i}", link="")
        for i in range(12)
    ]


def test_scores_all_articles_with_integer_ids(config, articles):
    client = fake_client(parsed_response(scores(*[(i, 0.1) for i in range(12)])))

    select_positive_articles(config=config, client=client, articles=articles)

    call = client.responses.calls[0]
    assert call["text_format"] is PositivityScores
    assert call["reasoning"] == {"effort": "none"}
    assert call["model"] == "gpt-6-luna"
    # Every article is sent (not just the first 8), keyed by short integer ids, no URLs.
    for i in range(12):
        assert f'"id": {i}' in call["input"]
    assert "https://rte.ie" not in call["input"]


def test_selects_top_scores_above_threshold(config, articles):
    response = scores((0, 0.2), (1, 0.95), (2, 0.6), (3, 0.59), (4, 0.8), (5, 0.7))
    selection = select_positive_articles(
        config=config, client=fake_client(parsed_response(response)), articles=articles
    )

    assert [p.article.title for p in selection.selected] == ["Story 1", "Story 4", "Story 5"]
    assert [p.score for p in selection.selected] == [0.95, 0.8, 0.7]
    # All scores persisted, mapped back to guids, in feed order.
    assert [s.guid for s in selection.scores] == [articles[i].guid for i in range(6)]
    assert selection.scores[3].score == 0.59


def test_threshold_is_inclusive_and_configurable(config, articles):
    config.positivity_min_score = 0.6
    selection = select_positive_articles(
        config=config,
        client=fake_client(parsed_response(scores((0, 0.6), (1, 0.59)))),
        articles=articles,
    )
    assert [p.article.guid for p in selection.selected] == [articles[0].guid]


def test_nothing_above_threshold_returns_empty_selection(config, articles):
    selection = select_positive_articles(
        config=config,
        client=fake_client(parsed_response(scores((0, 0.0), (1, 0.3)))),
        articles=articles,
    )
    assert selection.selected == []
    assert len(selection.scores) == 2


def test_ignores_unknown_and_duplicate_ids_and_clamps(config, articles):
    response = scores((0, 1.4), (0, 0.1), (99, 0.9), (-1, 0.9), (1, -0.2))
    selection = select_positive_articles(
        config=config, client=fake_client(parsed_response(response)), articles=articles
    )
    assert [(s.guid, s.score) for s in selection.scores] == [
        (articles[0].guid, 1.0),
        (articles[1].guid, 0.0),
    ]


def test_incomplete_response_raises(config, articles):
    client = fake_client(parsed_response(None, status="incomplete"))
    with pytest.raises(PositivityError, match="incomplete"):
        select_positive_articles(config=config, client=client, articles=articles)


def test_refusal_raises(config, articles):
    refusal = SimpleNamespace(content=[SimpleNamespace(type="refusal", refusal="no thanks")])
    client = fake_client(parsed_response(None, output=[refusal]))
    with pytest.raises(PositivityError, match="refused: no thanks"):
        select_positive_articles(config=config, client=client, articles=articles)


def test_api_error_raises(config, articles):
    client = fake_client(RuntimeError("boom"))
    with pytest.raises(PositivityError, match="boom"):
        select_positive_articles(config=config, client=client, articles=articles)


def test_no_usable_scores_raises(config, articles):
    client = fake_client(parsed_response(scores((50, 0.9))))
    with pytest.raises(PositivityError, match="no usable scores"):
        select_positive_articles(config=config, client=client, articles=articles)


def test_empty_reasoning_effort_omits_parameter(config, articles):
    config.openai.positivity_reasoning_effort = None
    client = fake_client(parsed_response(scores((0, 0.9))))
    select_positive_articles(config=config, client=client, articles=articles)
    assert "reasoning" not in client.responses.calls[0]


def test_no_articles_makes_no_call(config):
    client = fake_client(parsed_response(scores()))
    selection = select_positive_articles(config=config, client=client, articles=[])
    assert selection.selected == [] and selection.scores == []
    assert client.responses.calls == []
