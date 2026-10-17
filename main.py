from __future__ import annotations

import logging
import logging.handlers
import signal
from pathlib import Path

import typer
from openai import OpenAI

from epaper_goodnews.config import AppConfig, load_config
from epaper_goodnews.display_controller import DisplayController
from epaper_goodnews.job_manager import JobManager
from epaper_goodnews.pipeline import run_generation
from epaper_goodnews.scheduler import SchedulerService
from epaper_goodnews.storage import load_health
from epaper_goodnews.web.app import AppState, create_app

app = typer.Typer(help="E-Paper Good News controller")


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
    return OpenAI(api_key=config.openai.api_key, organization=config.openai.organization)


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
    )
    job_manager = JobManager()
    scheduler = SchedulerService(config.scheduler)
    return config, client, display, job_manager, scheduler


@app.command()
def generate():
    """Run the pipeline once. Set EPAPER_DEVICE=omni_epd.mock to skip hardware."""
    config, client, display, _, _ = init_components()
    display.initialize()
    metadata = run_generation(
        config=config,
        client=client,
        storage=config.storage,
        display=display,
    )
    typer.echo(f"Run completed with status: {metadata.status.value}")
    if metadata.errors:
        typer.echo("Errors:\n" + "\n".join(metadata.errors))
    display.close()


@app.command()
def serve(
    host: str | None = typer.Option(None),
    port: int | None = typer.Option(None),
):
    """Start the combined scheduler and web server. Set EPAPER_DEVICE=omni_epd.mock to skip hardware."""
    config, client, display, job_manager, scheduler = init_components()
    display.initialize()

    def job(cancel_event):
        run_generation(
            config=config,
            client=client,
            storage=config.storage,
            display=display,
            cancel_event=cancel_event,
        )

    def trigger() -> bool:
        return job_manager.start(job)

    def handle_sigterm(signum, frame):
        # systemd stops the service with SIGTERM; turn it into SystemExit so the
        # finally block below runs and an in-flight refresh can finish and sleep.
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)

    scheduler.start()
    scheduler.schedule_daily(trigger)
    scheduler.schedule_catch_up(trigger, lambda: load_health(config.storage).last_success)

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
