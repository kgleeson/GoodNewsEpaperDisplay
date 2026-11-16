import json
import os
from datetime import datetime
from pathlib import Path

from epaper_goodnews.config import _build_storage
from epaper_goodnews.models import GenerationMetadata, RunStatus
from epaper_goodnews.storage import (
    list_metadata_files,
    load_health,
    update_current,
    write_metadata,
)


def write_run(storage, stem, started, status, *, mtime, errors=()):
    path = storage.metadata_path / f"{stem}.json"
    path.write_text(
        json.dumps(
            {
                "run_id": stem[11:],
                "status": status,
                "started_at": started,
                "completed_at": started,
                "errors": list(errors),
            }
        )
    )
    os.utime(path, (mtime, mtime))
    return path


def test_same_day_runs_are_ordered_by_time_not_run_id(tmp_path):
    storage = _build_storage(tmp_path / "data")
    # The earlier run has the alphabetically later (random) id.
    write_run(
        storage, "2026-11-16-ffffffff", "2026-11-16T08:00:00", "failed", mtime=1000, errors=["boom"]
    )
    write_run(storage, "2026-11-16-00000000", "2026-11-16T09:30:00", "success", mtime=2000)
    write_run(storage, "2026-11-15-aaaaaaaa", "2026-11-15T08:00:00", "success", mtime=3000)

    assert [p.stem for p in list_metadata_files(storage)] == [
        "2026-11-16-00000000",
        "2026-11-16-ffffffff",
        "2026-11-15-aaaaaaaa",
    ]
    health = load_health(storage)
    assert health.last_status == RunStatus.SUCCESS
    assert health.last_run_id == "00000000"
    assert health.message is None


def test_skipped_run_counts_as_ok_but_not_as_success(tmp_path):
    storage = _build_storage(tmp_path / "data")
    write_run(storage, "2026-11-15-aaaaaaaa", "2026-11-15T08:01:00", "success", mtime=1000)
    write_run(storage, "2026-11-16-bbbbbbbb", "2026-11-16T08:00:30", "skipped", mtime=2000)

    health = load_health(storage)
    assert health.last_status == RunStatus.SKIPPED
    assert health.last_success == datetime(2026, 11, 15, 8, 1)
    assert health.last_ok == datetime(2026, 11, 16, 8, 0, 30)


def test_failed_run_is_not_ok(tmp_path):
    storage = _build_storage(tmp_path / "data")
    write_run(storage, "2026-11-16-bbbbbbbb", "2026-11-16T08:00:30", "failed", mtime=2000)
    health = load_health(storage)
    assert health.last_ok is None and health.last_success is None


def test_relative_data_dir_gives_working_current_links(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    storage = _build_storage(Path("data"))
    assert storage.base_path.is_absolute()
    image = storage.images_path / "a.png"
    image.write_bytes(b"png")
    meta = storage.metadata_path / "a.json"
    meta.write_text("{}")

    update_current(storage, image, meta)

    assert storage.current_image.read_bytes() == b"png"
    assert storage.current_metadata.read_text() == "{}"


def test_update_current_replaces_existing_links(tmp_path):
    storage = _build_storage(tmp_path / "data")
    for name in ("old", "new"):
        (storage.images_path / f"{name}.png").write_bytes(name.encode())
        (storage.metadata_path / f"{name}.json").write_text(name)
    update_current(storage, storage.images_path / "old.png", storage.metadata_path / "old.json")
    update_current(storage, storage.images_path / "new.png", storage.metadata_path / "new.json")

    assert storage.current_image.read_bytes() == b"new"
    assert storage.current_metadata.read_text() == "new"
    assert not list(storage.base_path.glob("*.tmp"))


def test_write_metadata_leaves_no_temp_file(tmp_path):
    storage = _build_storage(tmp_path / "data")
    metadata = GenerationMetadata(run_id="abc12345", started_at=datetime(2026, 11, 16, 8, 0))
    path = write_metadata(metadata, storage)
    assert path.name == "2026-11-16-abc12345.json"
    assert json.loads(path.read_text())["run_id"] == "abc12345"
    assert [p.name for p in storage.metadata_path.iterdir()] == [path.name]
