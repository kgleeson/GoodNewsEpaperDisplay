from datetime import datetime
from pathlib import Path

import pytest

import main
from epaper_goodnews.config import AppConfig, OpenAIConfig, _build_storage
from epaper_goodnews.models import GenerationMetadata, ImageResult, RunStatus


def run(status, *, image=False):
    metadata = GenerationMetadata(run_id="r", started_at=datetime(2026, 11, 16, 8, 0))
    metadata.status = status
    if image:
        metadata.image = ImageResult(image_path=Path("x.png"), prompt="p")
    return metadata


def test_failed_scheduled_run_is_retried_with_growing_delays():
    delays = [main.retry_delay(run(RunStatus.FAILED), attempt) for attempt in range(3)]
    assert delays == [*main.RETRY_DELAYS_SECONDS, None]


@pytest.mark.parametrize("status", [RunStatus.SUCCESS, RunStatus.SKIPPED, RunStatus.CANCELLED])
def test_only_failures_are_retried(status):
    assert main.retry_delay(run(status), 0) is None


def test_display_failure_is_not_retried():
    # The image exists and was paid for; a retry would generate another one.
    assert main.retry_delay(run(RunStatus.FAILED, image=True), 0) is None


def test_openai_client_has_bounded_timeout_and_retries(tmp_path):
    config = AppConfig(
        openai=OpenAIConfig(api_key="test"), storage=_build_storage(tmp_path / "data")
    )
    client = main.create_client(config)
    assert client.timeout.read == main.OPENAI_TIMEOUT_SECONDS
    assert client.timeout.connect == main.OPENAI_CONNECT_TIMEOUT_SECONDS
    assert client.max_retries == main.OPENAI_MAX_RETRIES


class _FakeDisplay:
    closed = False

    def initialize(self):
        return True

    def close(self):
        self.closed = True


@pytest.mark.parametrize(
    ("status", "exit_code"),
    [(RunStatus.SUCCESS, 0), (RunStatus.SKIPPED, 0), (RunStatus.FAILED, 1)],
)
def test_generate_exit_code_reflects_the_run(monkeypatch, tmp_path, status, exit_code):
    from typer.testing import CliRunner

    config = AppConfig(
        openai=OpenAIConfig(api_key="test"), storage=_build_storage(tmp_path / "data")
    )
    display = _FakeDisplay()
    monkeypatch.setattr(main, "init_components", lambda: (config, None, display, None, None))
    monkeypatch.setattr(main, "run_generation", lambda **kwargs: run(status))

    result = CliRunner().invoke(main.app, ["generate"])

    assert result.exit_code == exit_code
    assert f"status: {status.value}" in result.output
    assert display.closed
