from __future__ import annotations

import json
import logging
import os
from dataclasses import fields, is_dataclass
from datetime import datetime
from pathlib import Path

from .config import StorageConfig
from .models import GenerationMetadata, HealthStatus, RunStatus

LOGGER = logging.getLogger(__name__)


def _serialize(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return {field.name: _serialize(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize(val) for key, val in value.items()}
    return value


def _write_atomic(path: Path, data: bytes) -> None:
    """Write via a temp file and rename, so a power cut never leaves a half-written file."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def write_metadata(metadata: GenerationMetadata, storage: StorageConfig) -> Path:
    date_key = metadata.started_at.strftime("%Y-%m-%d")
    filename = f"{date_key}-{metadata.run_id}.json"
    output_path = storage.metadata_path / filename
    payload = _serialize(metadata)
    _write_atomic(output_path, json.dumps(payload, indent=2).encode("utf-8"))
    LOGGER.info("Wrote metadata to %s", output_path)
    return output_path


def update_current(storage: StorageConfig, image_path: Path, metadata_path: Path) -> None:
    pairs = [
        (storage.current_image, image_path),
        (storage.current_metadata, metadata_path),
    ]
    for target, source in pairs:
        # Build the new link beside the old one and rename it into place, so `current.*`
        # always exists. The link target is absolute so it works from any directory.
        tmp = target.with_name(target.name + ".tmp")
        tmp.unlink(missing_ok=True)
        try:
            os.symlink(source.resolve(), tmp)
            os.replace(tmp, target)
        except OSError:
            LOGGER.debug("Symlink unsupported, copying %s to %s", source, target)
            if not source.is_file():  # pragma: no cover - defensive
                raise
            _write_atomic(target, source.read_bytes())
    LOGGER.debug(
        "Updated current references to %s and %s", storage.current_image, storage.current_metadata
    )


def _newest_first_key(path: Path) -> tuple[str, float, str]:
    # Names are YYYY-MM-DD-<random run id>, so the name only orders runs by day. Within a
    # day the file's modification time (written once, when the run ends) gives the order.
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (path.stem[:10], mtime, path.stem)


def list_metadata_files(storage: StorageConfig) -> list[Path]:
    """Metadata files, newest run first."""
    files = list(storage.metadata_path.glob("*.json"))
    files.sort(key=_newest_first_key, reverse=True)
    return files


def load_health(storage: StorageConfig) -> HealthStatus:
    last_run = None
    last_run_id = None
    last_success = None
    last_ok = None
    last_status = None
    message = None

    for path in list_metadata_files(storage):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            status = RunStatus(data.get("status", RunStatus.SUCCESS))
            completed = data.get("completed_at")
            started = data.get("started_at")
            if started and not last_run:
                last_run = datetime.fromisoformat(started)
                last_run_id = data.get("run_id")
                last_status = status
                message = ", ".join(data.get("errors", [])) or None
            # A skipped run (no good news) did its job for the day, like a success.
            if status in (RunStatus.SUCCESS, RunStatus.SKIPPED) and completed and not last_ok:
                last_ok = datetime.fromisoformat(completed)
            if status == RunStatus.SUCCESS and completed and not last_success:
                last_success = datetime.fromisoformat(completed)
        except Exception:  # pragma: no cover - defensive
            continue
        if last_run and last_success:
            break

    return HealthStatus(
        last_success=last_success,
        last_run=last_run,
        last_status=last_status,
        message=message,
        last_ok=last_ok,
        last_run_id=last_run_id,
    )
