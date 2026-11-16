from __future__ import annotations

import base64
import json
import os
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from epaper_goodnews import pipeline
from epaper_goodnews.config import AppConfig, OpenAIConfig, _build_storage
from epaper_goodnews.headline import HeadlinePayload
from epaper_goodnews.models import Article, RunStatus
from epaper_goodnews.positivity import PositivityScores, ScoredItem


def _b64_png():
    buffer = BytesIO()
    Image.new("RGB", (1600, 960), (0, 200, 0)).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


class FakeClient:
    """Fake OpenAI client: responses.parse returns by text_format, images.generate a PNG."""

    def __init__(self, positivity: list[float]):
        self.parse_calls: list[dict] = []
        self.image_calls: list[dict] = []
        self._positivity = positivity
        self.responses = SimpleNamespace(parse=self._parse)
        self.images = SimpleNamespace(generate=self._generate, edit=self._generate)

    def _parse(self, **kwargs):
        self.parse_calls.append(kwargs)
        if kwargs["text_format"] is PositivityScores:
            parsed = PositivityScores(
                items=[
                    ScoredItem(id=i, positivity=p, rationale="r")
                    for i, p in enumerate(self._positivity)
                ]
            )
        else:
            parsed = HeadlinePayload(headline="Good day", subheading="Indeed.")
        return SimpleNamespace(
            id="r", status="completed", output=[], output_parsed=parsed, usage=None
        )

    def _generate(self, **kwargs):
        self.image_calls.append(kwargs)
        return SimpleNamespace(id="i", data=[SimpleNamespace(b64_json=_b64_png())], usage=None)


class FakeDisplay:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.shown: list = []

    def display_image(self, path, *, cancel_event=None):
        self.shown.append(path)
        if self.fail:
            raise RuntimeError("panel unplugged")


@pytest.fixture
def config(tmp_path):
    return AppConfig(
        openai=OpenAIConfig(api_key="test"),
        storage=_build_storage(tmp_path / "data"),
    )


@pytest.fixture(autouse=True)
def fake_feed(monkeypatch):
    articles = [Article(guid=f"g{i}", title=f"T{i}", summary="", link="") for i in range(5)]
    monkeypatch.setattr(pipeline, "fetch_articles", lambda *a, **k: articles)
    return articles


@pytest.fixture
def yesterday(config):
    """An existing current.png/current.json from a previous successful run."""
    storage = config.storage
    image = storage.images_path / "2026-10-16-old.png"
    Image.new("RGB", (800, 480)).save(image)
    meta = storage.metadata_path / "2026-10-16-old.json"
    meta.write_text(json.dumps({"run_id": "old"}))
    os.symlink(image, storage.current_image)
    os.symlink(meta, storage.current_metadata)
    return image, meta


def _run(config, client, display):
    return pipeline.run_generation(
        config=config, client=client, storage=config.storage, display=display
    )


def test_no_good_news_skips_generation_and_redisplays_current(config, yesterday):
    client = FakeClient([0.1, 0.0, 0.59, 0.3, 0.2])
    display = FakeDisplay()

    metadata = _run(config, client, display)

    assert metadata.status == RunStatus.SKIPPED
    assert metadata.headline is None and metadata.image is None
    assert len(client.parse_calls) == 1  # positivity only, no headline call
    assert client.image_calls == []
    assert display.shown == [config.storage.current_image]
    # current.* still point at yesterday's files.
    assert os.readlink(config.storage.current_image) == str(yesterday[0])
    assert os.readlink(config.storage.current_metadata) == str(yesterday[1])
    # All scores persisted in the run's own metadata file.
    written = json.loads(
        next(config.storage.metadata_path.glob(f"*-{metadata.run_id}.json")).read_text()
    )
    assert written["status"] == "skipped"
    assert len(written["article_scores"]) == 5
    assert written["positivity_min_score"] == 0.6


def test_skip_without_current_image_does_not_touch_display(config):
    display = FakeDisplay()
    metadata = _run(config, FakeClient([0.0] * 5), display)
    assert metadata.status == RunStatus.SKIPPED
    assert display.shown == []


def test_skip_with_redisplay_failure_is_failed(config, yesterday):
    metadata = _run(config, FakeClient([0.0] * 5), FakeDisplay(fail=True))
    assert metadata.status == RunStatus.FAILED
    assert os.readlink(config.storage.current_image) == str(yesterday[0])


def test_success_updates_current(config, yesterday):
    client = FakeClient([0.9, 0.1, 0.7, 0.2, 0.8])
    display = FakeDisplay()

    metadata = _run(config, client, display)

    assert metadata.status == RunStatus.SUCCESS
    assert [p.article.guid for p in metadata.positive_articles] == ["g0", "g4", "g2"]
    assert len(metadata.article_scores) == 5
    assert display.shown == [metadata.image.image_path]
    assert os.readlink(config.storage.current_image) == str(metadata.image.image_path)


def test_display_failure_does_not_repoint_current(config, yesterday):
    metadata = _run(config, FakeClient([0.9] * 5), FakeDisplay(fail=True))
    assert metadata.status == RunStatus.FAILED
    assert metadata.image is not None
    assert os.readlink(config.storage.current_image) == str(yesterday[0])
    assert os.readlink(config.storage.current_metadata) == str(yesterday[1])


def test_invalid_image_size_fails_before_any_api_call(config):
    config.openai.image_size = "1601x960"
    client = FakeClient([0.9] * 5)
    metadata = _run(config, client, FakeDisplay())
    assert metadata.status == RunStatus.FAILED
    assert client.parse_calls == [] and client.image_calls == []


def test_cancel_noticed_inside_a_stage_is_recorded_as_cancelled(config, monkeypatch):
    import threading

    cancel = threading.Event()
    client = FakeClient([0.9] * 5)
    original = client.responses.parse

    def parse_then_cancel(**kwargs):
        cancel.set()  # the user hits Regenerate while the positivity call is in flight
        return original(**kwargs)

    client.responses.parse = parse_then_cancel
    display = FakeDisplay()
    metadata = pipeline.run_generation(
        config=config, client=client, storage=config.storage, display=display, cancel_event=cancel
    )
    assert metadata.status == RunStatus.CANCELLED
    assert metadata.errors == ["Job cancelled"]
    assert client.image_calls == [] and display.shown == []


def test_cancel_while_waiting_for_the_panel_is_cancelled_not_failed(config, yesterday):
    from epaper_goodnews.display_controller import RefreshCancelledError

    class CancelledDisplay:
        def display_image(self, path, *, cancel_event=None):
            raise RefreshCancelledError("Cancelled while waiting to refresh the panel")

    metadata = _run(config, FakeClient([0.9] * 5), CancelledDisplay())
    assert metadata.status == RunStatus.CANCELLED
    assert os.readlink(config.storage.current_image) == str(yesterday[0])


def test_run_times_use_the_schedule_zone_not_the_system_zone(config):
    from datetime import datetime, timedelta, timezone

    config.scheduler.timezone = "Pacific/Kiritimati"  # UTC+14, no DST
    metadata = _run(config, FakeClient([0.0] * 5), FakeDisplay())
    expected = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=14)
    assert metadata.started_at.tzinfo is None
    assert abs(metadata.started_at - expected) < timedelta(minutes=1)
    assert abs(metadata.completed_at - expected) < timedelta(minutes=1)


def test_image_file_shares_the_metadata_files_name(config):
    metadata = _run(config, FakeClient([0.9] * 5), FakeDisplay())
    meta_file = next(config.storage.metadata_path.glob(f"*-{metadata.run_id}.json"))
    assert metadata.image.image_path.stem == meta_file.stem
