from __future__ import annotations

import logging
import logging.handlers
import signal
from pathlib import Path
from typing import Optional

import typer
from openai import OpenAI, Timeout

from epaper_goodnews.config import AppConfig, load_config
from epaper_goodnews.display_controller import DisplayController
from epaper_goodnews.job_manager import JobManager
from epaper_goodnews.models import GenerationMetadata, RunStatus
from epaper_goodnews.pipeline import run_generation
from epaper_goodnews.scheduler import SchedulerService
from epaper_goodnews.storage import load_health
from epaper_goodnews.web.app import AppState, create_app

app = typer.Typer(help="E-Paper Good News controller")

LOGGER = logging.getLogger(__name__)

# A normal image request takes under a minute. Without a limit the SDK waits 10 minutes
# per attempt and retries twice, so one stalled call could block the job for half an hour.
OPENAI_TIMEOUT_SECONDS = 300.0
OPENAI_CONNECT_TIMEOUT_SECONDS = 20.0
OPENAI_MAX_RETRIES = 1

# A scheduled run that fails is tried again after each of these delays.
RETRY_DELAYS_SECONDS = (30 * 60, 2 * 60 * 60)


def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "app.log"
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=2 * 1024 * 1024, backupCount=5
    )
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler()])
    # Per-request access lines made up ~40% of app.log; keep warnings and errors only.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)


def create_client(config: AppConfig) -> OpenAI:
    if not config.openai.api_key:
        raise RuntimeError("OPENAI_API_KEY not set in environment")
    return OpenAI(
        api_key=config.openai.api_key,
        organization=config.openai.organization,
        timeout=Timeout(OPENAI_TIMEOUT_SECONDS, connect=OPENAI_CONNECT_TIMEOUT_SECONDS),
        max_retries=OPENAI_MAX_RETRIES,
    )


def retry_delay(metadata: GenerationMetadata, attempt: int) -> int | None:
    """Seconds to wait before retrying a scheduled run, or None if it shouldn't be retried.

    `attempt` counts retries already made. A run that got as far as producing an image
    (so the panel refresh is what failed) is not retried: that would pay for a second
    image, and the saved one can be pushed from the history page instead.
    """
    if metadata.status != RunStatus.FAILED or metadata.image is not None:
        return None
    if attempt >= len(RETRY_DELAYS_SECONDS):
        return None
    return RETRY_DELAYS_SECONDS[attempt]


def init_components(
    env_path: Path | None = None,
) -> tuple[AppConfig, OpenAI, DisplayController, JobManager, SchedulerService]:
    config = load_config(env_path)
    setup_logging(config.storage.logs_path)
    client = create_client(config)
    display = DisplayController(
        device_type=config.device_type,
        saturation=config.display_saturation,
        brightness=config.display_brightness,
        contrast=config.display_contrast,
        gamma=config.display_gamma,
        colour_mapping=config.display_colour_mapping,
    )
    job_manager = JobManager()
    scheduler = SchedulerService(config.scheduler)
    return config, client, display, job_manager, scheduler


@app.command()
def generate():
    """Run the pipeline once. Set EPAPER_DEVICE=omni_epd.mock to skip hardware."""
    config, client, display, _, _ = init_components()
    display.initialize()
    try:
        metadata = run_generation(
            config=config,
            client=client,
            storage=config.storage,
            display=display,
        )
    finally:
        display.close()
    typer.echo(f"Run completed with status: {metadata.status.value}")
    if metadata.errors:
        typer.echo("Errors:\n" + "\n".join(metadata.errors))
    # A skip (no good news) is a completed run; only a failure is an error for the caller.
    if metadata.status == RunStatus.FAILED:
        raise typer.Exit(code=1)


@app.command()
def serve(
    # Typer 0.12 can't parse `X | None` option types; keep Optional here.
    host: Optional[str] = typer.Option(None),  # noqa: UP045
    port: Optional[int] = typer.Option(None),  # noqa: UP045
):
    """Start the combined scheduler and web server. Set EPAPER_DEVICE=omni_epd.mock to skip hardware."""
    config, client, display, job_manager, scheduler = init_components()
    display.initialize()

    def last_ok():
        return load_health(config.storage).last_ok

    def make_job(attempt: int | None):
        """A pipeline job. `attempt` is None for manual runs, which are never retried."""

        def job(cancel_event):
            metadata = run_generation(
                config=config,
                client=client,
                storage=config.storage,
                display=display,
                cancel_event=cancel_event,
            )
            delay = None if attempt is None else retry_delay(metadata, attempt)
            if delay is not None:
                LOGGER.warning("Scheduled run failed; retrying in %d min", delay // 60)
                scheduler.schedule_once(lambda: retry(attempt + 1), delay, job_id="retry")

        return job

    def retry(attempt: int) -> None:
        if job_manager.is_running():
            LOGGER.info("Skipping retry: a run is already in progress")
        elif not scheduler.missed_today(last_ok()):
            LOGGER.info("Skipping retry: a run has completed since the failure")
        else:
            job_manager.start(make_job(attempt))

    def trigger() -> bool:
        return job_manager.start(make_job(None))

    def scheduled_trigger() -> bool:
        return job_manager.start(make_job(0))

    def handle_sigterm(signum, frame):
        # systemd stops the service with SIGTERM; turn it into SystemExit so the
        # finally block below runs and an in-flight refresh can finish and sleep.
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)

    scheduler.start()
    scheduler.schedule_daily(scheduled_trigger)
    scheduler.schedule_catch_up(scheduled_trigger, last_ok)

    state = AppState(
        config=config,
        storage=config.storage,
        job_manager=job_manager,
        scheduler=scheduler,
        trigger_generation=trigger,
        display=display,
    )
    flask_app = create_app(state)

    host_value = host or config.web.host
    port_value = port or config.web.port
    try:
        flask_app.run(host=host_value, port=port_value)
    finally:
        scheduler.shutdown()
        display.close()


if __name__ == "__main__":
    app()
