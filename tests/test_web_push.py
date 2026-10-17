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
