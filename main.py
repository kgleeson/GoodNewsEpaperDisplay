from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from typing import Optional

import typer

from openai import OpenAI

from epaper_goodnews.config import AppConfig, load_config
from epaper_goodnews.display_controller import DisplayController
from epaper_goodnews.job_manager import JobManager
from epaper_goodnews.pipeline import run_generation
from epaper_goodnews.scheduler import SchedulerService
from epaper_goodnews.web.app import AppState, create_app

app = typer.Typer(help="E-Paper Good News controller")


class _NullDisplay(DisplayController):
    def initialize(self) -> None:  # type: ignore[override]
        logging.info("Display disabled; skipping hardware initialization")

    def display_image(self, image_path: Path) -> None:  # type: ignore[override]
        logging.info("Display disabled; skipping image update for %s", image_path)


def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "app.log"
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=2 * 1024 * 1024, backupCount=5
    )
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler()])


def create_client(config: AppConfig) -> OpenAI:
    if not config.openai.api_key:
        raise RuntimeError("OPENAI_API_KEY not set in environment")
    return OpenAI(api_key=config.openai.api_key, organization=config.openai.organization)


def init_components(
    env_path: Optional[Path] = None,
    *,
    with_display: bool = True,
) -> tuple[AppConfig, OpenAI, DisplayController, JobManager, SchedulerService]:
    config = load_config(env_path)
    setup_logging(config.storage.logs_path)
    client = create_client(config)
    display: DisplayController
    if with_display:
        display = DisplayController()
    else:
        display = _NullDisplay()
    job_manager = JobManager()
    scheduler = SchedulerService(config.scheduler)
    return config, client, display, job_manager, scheduler


@app.command()
def generate(skip_display: bool = typer.Option(False, help="Skip pushing to the ePaper display")):
    """Run the pipeline once."""
    config, client, display, _, _ = init_components(with_display=not skip_display)
    display.initialize()
    metadata = run_generation(
        config=config,
        client=client,
        storage=config.storage,
        display=display,
    )
    typer.echo(f"Run completed with status: {metadata.status.value}")
    if metadata.errors:
        typer.echo("Errors: \n" + "\n".join(metadata.errors))


@app.command()
def serve(
    host: Optional[str] = typer.Option(None),
    port: Optional[int] = typer.Option(None),
    skip_display: bool = typer.Option(False, help="Run without pushing updates to the ePaper display"),
):
    """Start the combined scheduler and web server."""
    config, client, display, job_manager, scheduler = init_components(with_display=not skip_display)
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

    scheduler.start()
    scheduler.schedule_daily(trigger)

    state = AppState(
        config=config,
        storage=config.storage,
        job_manager=job_manager,
        scheduler=scheduler,
        trigger_generation=trigger,
    )
    flask_app = create_app(state)

    host_value = host or config.web.host
    port_value = port or config.web.port
    try:
        flask_app.run(host=host_value, port=port_value)
    finally:
        scheduler.shutdown()


if __name__ == "__main__":
    app()
