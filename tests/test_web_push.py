import json
import os
from types import SimpleNamespace

from epaper_goodnews.config import _build_storage
from epaper_goodnews.display_controller import DisplayBusyError
from epaper_goodnews.web.app import AppState, create_app


class BusyDisplay:
    last_refresh_at = None
    last_error = None

    def display_image(self, path, *, wait=True):
        assert wait is False  # manual pushes must not queue behind a refresh
        raise DisplayBusyError("Display is busy with another refresh")


def make_client(tmp_path, display):
    storage = _build_storage(tmp_path / "data")
    (storage.images_path / "2026-10-17-abc.png").write_bytes(b"png")
    state = AppState(
        config=SimpleNamespace(),
        storage=storage,
        job_manager=SimpleNamespace(is_running=lambda: False),
        scheduler=SimpleNamespace(next_run="2026-10-18T08:00:00+01:00"),
        trigger_generation=lambda: True,
        display=display,
    )
    return create_app(state).test_client()


def test_push_returns_409_when_busy(tmp_path):
    client = make_client(tmp_path, BusyDisplay())
    resp = client.post("/push/2026-10-17-abc", headers={"Accept": "application/json"})
    assert resp.status_code == 409
    assert resp.get_json()["ok"] is False


def test_health_reports_display_state(tmp_path):
    client = make_client(tmp_path, BusyDisplay())
    body = client.get("/health").get_json()
    assert body["last_display_refresh"] is None
    assert body["next_run"] == "2026-10-18T08:00:00+01:00"


def _write_run(storage, stem, status, *, mtime, errors=()):
    path = storage.metadata_path / f"{stem}.json"
    path.write_text(
        json.dumps(
            {
                "run_id": stem[11:],
                "status": status,
                "started_at": f"{stem[:10]}T08:00:00",
                "completed_at": f"{stem[:10]}T08:01:00",
                "errors": list(errors),
                "headline": {"headline": "Good day", "subheading": "Indeed."},
            }
        )
    )
    os.utime(path, (mtime, mtime))
    return path


def test_api_current_reports_a_failed_latest_run_separately(tmp_path):
    client = make_client(tmp_path, BusyDisplay())
    storage = _build_storage(tmp_path / "data")
    good = _write_run(storage, "2026-10-16-good0000", "success", mtime=1000)
    _write_run(storage, "2026-10-17-bad00000", "failed", mtime=2000, errors=["OpenAI is down"])
    os.symlink(good, storage.current_metadata)

    body = client.get("/api/current").get_json()

    # The edition on show is still the last success...
    assert body["run_id"] == "good0000" and body["status"] == "success"
    # ...but the run that just finished failed, and the page must be able to say so.
    assert body["last_run"] == {
        "run_id": "bad00000",
        "status": "failed",
        "message": "OpenAI is down",
    }


def test_index_exposes_the_latest_run_id_for_polling(tmp_path):
    client = make_client(tmp_path, BusyDisplay())
    storage = _build_storage(tmp_path / "data")
    _write_run(storage, "2026-10-17-bad00000", "failed", mtime=2000)
    assert b'data-last-run-id="bad00000"' in client.get("/").data


def test_health_reports_last_ok_for_a_skipped_day(tmp_path):
    client = make_client(tmp_path, BusyDisplay())
    storage = _build_storage(tmp_path / "data")
    _write_run(storage, "2026-10-17-skip0000", "skipped", mtime=2000)
    body = client.get("/health").get_json()
    assert body["last_ok"] == "2026-10-17T08:01:00"
    assert body["last_success"] is None


def test_bad_query_values_do_not_error(tmp_path):
    client = make_client(tmp_path, BusyDisplay())
    assert client.get("/history?page=abc").status_code == 200
    assert client.get("/api/history?limit=abc").status_code == 200
