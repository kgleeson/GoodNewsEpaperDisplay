from __future__ import annotations

import json
import logging
import os
from dataclasses import fields, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

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


def write_metadata(metadata: GenerationMetadata, storage: StorageConfig) -> Path:
    date_key = metadata.started_at.strftime("%Y-%m-%d")
    filename = f"{date_key}-{metadata.run_id}.json"
    output_path = storage.metadata_path / filename
    payload = _serialize(metadata)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    LOGGER.info("Wrote metadata to %s", output_path)
    return output_path


def update_current(storage: StorageConfig, image_path: Path, metadata_path: Path) -> None:
    pairs = [
        (storage.current_image, image_path),
        (storage.current_metadata, metadata_path),
    ]
    for target, source in pairs:
        if target.exists() or target.is_symlink():
            target.unlink()
        try:
            os.symlink(source, target)
        except OSError:
            LOGGER.debug("Symlink unsupported, copying %s to %s", source, target)
            if source.is_file():
                target.write_text(source.read_text(encoding="utf-8")) if source.suffix == ".json" else target.write_bytes(source.read_bytes())
            else:  # pragma: no cover - defensive
                raise
    LOGGER.debug("Updated current references to %s and %s", storage.current_image, storage.current_metadata)


def list_metadata_files(storage: StorageConfig) -> List[Path]:
    files = list(storage.metadata_path.glob("*.json"))
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return files


def load_health(storage: StorageConfig) -> HealthStatus:
    last_run = None
    last_success = None
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
                last_status = status
                message = ", ".join(data.get("errors", [])) or None
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
    )
