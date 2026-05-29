from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from ..config import AppConfig
from ..job_manager import JobManager
from ..scheduler import SchedulerService
from ..storage import StorageConfig, list_metadata_files, load_health

LOGGER = logging.getLogger(__name__)


@dataclass
class AppState:
    config: AppConfig
    storage: StorageConfig
    job_manager: JobManager
    scheduler: SchedulerService
    trigger_generation: Callable[[], bool]


def _load_metadata(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.warning("Failed to load metadata %s: %s", path, exc)
        return None


def create_app(state: AppState) -> Flask:
    app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"))

    @app.get("/")
    def index():
        current_meta = _load_metadata(state.storage.current_metadata)
        image_url = None
        if current_meta and state.storage.current_image.exists():
            image_url = url_for("current_image")
        return render_template(
            "index.html",
            metadata=current_meta,
            image_url=image_url,
            job_running=state.job_manager.is_running(),
            next_run=state.scheduler.next_run,
        )

    @app.get("/history")
    def history():
        page = max(int(request.args.get("page", "1")), 1)
        per_page = 5
        files = list_metadata_files(state.storage)
        start = (page - 1) * per_page
        end = start + per_page
        slice_files = files[start:end]
        entries: List[dict] = []
        for path in slice_files:
            data = _load_metadata(path)
            if data:
                data["metadata_path"] = path.name
                image_info = data.get("image")
                if image_info and image_info.get("image_path"):
                    filename = Path(image_info["image_path"]).name
                    data["image_url"] = url_for("image_file", filename=filename)
                entries.append(data)
        has_next = end < len(files)
        has_prev = start > 0
        return render_template(
            "history.html",
            entries=entries,
            page=page,
            has_next=has_next,
            has_prev=has_prev,
        )

    @app.get("/metadata/<path:filename>")
    def metadata_file(filename: str):
        metadata_dir = state.storage.metadata_path
        target = (metadata_dir / filename).resolve()
        if metadata_dir.resolve() not in target.parents:
            abort(404)
        if not target.exists():
            abort(404)
        return send_from_directory(metadata_dir, filename, mimetype="application/json")

    @app.get("/images/<path:filename>")
    def image_file(filename: str):
        images_dir = state.storage.images_path
        target = (images_dir / filename).resolve()
        if images_dir.resolve() not in target.parents:
            abort(404)
        if not target.exists():
            abort(404)
        return send_from_directory(images_dir, filename)

    @app.get("/current-image")
    def current_image():
        if not state.storage.current_image.exists():
            abort(404)
        return send_from_directory(state.storage.current_image.parent, state.storage.current_image.name)

    @app.post("/generate")
    def generate():
        started = state.trigger_generation()
        wants_json = request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html
        payload = {"started": started, "running": state.job_manager.is_running()}
        if wants_json or request.is_json:
            return jsonify(payload)
        return redirect(url_for("index"))

    @app.get("/api/current")
    def api_current():
        metadata = _load_metadata(state.storage.current_metadata) or {}
        metadata["job_running"] = state.job_manager.is_running()
        metadata["next_run"] = state.scheduler.next_run
        if state.storage.current_image.exists():
            metadata["image"] = url_for("current_image")
        return jsonify(metadata)

    @app.get("/api/history")
    def api_history():
        limit = int(request.args.get("limit", "10"))
        files = list_metadata_files(state.storage)[:limit]
        payload = []
        for path in files:
            data = _load_metadata(path)
            if data:
                data["metadata"] = url_for("metadata_file", filename=path.name)
                payload.append(data)
        return jsonify(payload)

    @app.get("/health")
    def health():
        status = load_health(state.storage)
        return jsonify(
            {
                "last_success": status.last_success.isoformat() if status.last_success else None,
                "last_run": status.last_run.isoformat() if status.last_run else None,
                "last_status": status.last_status.value if status.last_status else None,
                "message": status.message,
            }
        )

    return app
