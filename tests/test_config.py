import pytest

from epaper_goodnews.config import load_config


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    """An empty .env, so the test never reads the project's real one."""
    monkeypatch.setenv("EPAPER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("EPAPER_DISPLAY_PAPER_WHITE", raising=False)
    path = tmp_path / ".env"
    path.write_text("")
    return path


def test_paper_white_defaults_to_on(env_file):
    assert load_config(env_file).display_paper_white is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [("true", True), ("1", True), ("", True), ("false", False), ("0", False), ("Off", False)],
)
def test_paper_white_setting(env_file, monkeypatch, value, expected):
    monkeypatch.setenv("EPAPER_DISPLAY_PAPER_WHITE", value)
    assert load_config(env_file).display_paper_white is expected
