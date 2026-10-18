import sys
import threading
import types
from pathlib import Path

import pytest
from PIL import Image

from epaper_goodnews import display_controller as dc
from epaper_goodnews.display_controller import (
    DisplayBusyError,
    DisplayController,
    PanelTimeoutError,
    check_dither_config,
)


@pytest.fixture
def image_path(tmp_path: Path) -> Path:
    path = tmp_path / "img.png"
    Image.new("RGB", (800, 480), (200, 120, 60)).save(path)
    return path


def write_ini(tmp_path: Path, display_section: str) -> Path:
    path = tmp_path / "omni-epd.ini"
    path.write_text(f"[EPD]\nmode=color\n\n[Display]\n{display_section}\n")
    return path


@pytest.mark.parametrize(
    ("section", "valid"),
    [
        ("", True),
        ("dither=Bayer\ndither_args=4,4", True),
        ("dither=FloydSteinberg", True),
        ("dither=Bayer4x4", False),
        ("dither=Bayer8x8", False),
    ],
)
def test_check_dither_config(tmp_path, section, valid):
    error = check_dither_config(write_ini(tmp_path, section))
    assert (error is None) is valid


def test_repo_ini_is_valid():
    assert check_dither_config(Path(__file__).parents[1] / "omni-epd.ini") is None


def test_initialize_flags_invalid_dither(tmp_path):
    ctrl = DisplayController("omni_epd.mock", ini_path=write_ini(tmp_path, "dither=Bayer4x4"))
    assert ctrl.initialize() is False
    assert "Bayer4x4" in ctrl.last_error


def test_mock_preview_uses_panel_palette(tmp_path, image_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    out = tmp_path / "mock.png"
    ctrl = DisplayController("omni_epd.mock", mock_output=out, colour_mapping="driver")
    ctrl.display_image(image_path)
    colours = {c for _, c in Image.open(out).convert("RGB").getcolors(maxcolors=256)}
    palette = {tuple(dc.PANEL_PALETTE[i : i + 3]) for i in range(0, 21, 3)}
    assert colours <= palette
    assert ctrl.last_refresh_at is not None


def test_push_refuses_while_busy(image_path, monkeypatch):
    ctrl = DisplayController("omni_epd.mock")
    started, release = threading.Event(), threading.Event()

    def slow_refresh(path):
        started.set()
        release.wait(5)

    monkeypatch.setattr(ctrl, "_refresh", slow_refresh)
    worker = threading.Thread(target=ctrl.display_image, args=(image_path,))
    worker.start()
    started.wait(5)
    with pytest.raises(DisplayBusyError):
        ctrl.display_image(image_path, wait=False)
    release.set()
    worker.join(5)


def test_push_refuses_too_soon_after_refresh(image_path, monkeypatch):
    ctrl = DisplayController("omni_epd.mock")
    monkeypatch.setattr(ctrl, "_refresh", lambda path: None)
    ctrl.display_image(image_path)
    ctrl._last_refresh_monotonic = dc.time.monotonic()
    with pytest.raises(DisplayBusyError, match="too recent"):
        ctrl.display_image(image_path, wait=False)


class FakeEpdConfig(types.ModuleType):
    def __init__(self, busy_value):
        super().__init__("waveshare_epd.epdconfig")
        self.busy_value = busy_value
        self.exits = 0

    def digital_read(self, pin):
        return self.busy_value

    def delay_ms(self, ms):
        pass

    def module_exit(self):
        self.exits += 1


class FakeDevice:
    busy_pin = 24

    def ReadBusyH(self):  # replaced by the controller
        raise AssertionError("stock busy-wait should not be used")


class FakeEpd:
    def __init__(self, fail_display=False):
        self._device = FakeDevice()
        self.fail_display = fail_display
        self.calls = []

    def prepare(self):
        self.calls.append("prepare")

    def display(self, img):
        self.calls.append("display")
        if self.fail_display:
            raise OSError("SPI write failed")
        self._device.ReadBusyH()

    def sleep(self):
        self.calls.append("sleep")


def run_with_fake_panel(monkeypatch, image_path, epd, epdconfig, timeout=0.05):
    monkeypatch.setitem(sys.modules, "waveshare_epd.epdconfig", epdconfig)
    fake_factory = types.SimpleNamespace(load_display_driver=lambda name: epd)
    monkeypatch.setitem(sys.modules, "omni_epd", types.SimpleNamespace(displayfactory=fake_factory))
    monkeypatch.setattr(dc, "BUSY_TIMEOUT_SECONDS", timeout)
    ctrl = DisplayController("waveshare_epd.epd7in3f")
    ctrl.display_image(image_path)
    return ctrl


def test_busy_timeout_powers_off_instead_of_hanging(monkeypatch, image_path):
    epdconfig, epd = FakeEpdConfig(busy_value=0), FakeEpd()
    with pytest.raises(PanelTimeoutError):
        run_with_fake_panel(monkeypatch, image_path, epd, epdconfig)
    assert epd.calls == ["prepare", "display"]  # no commands sent after the timeout
    assert epdconfig.exits == 1


def test_panel_sleeps_after_display_error(monkeypatch, image_path):
    epdconfig, epd = FakeEpdConfig(busy_value=1), FakeEpd(fail_display=True)
    with pytest.raises(OSError):
        run_with_fake_panel(monkeypatch, image_path, epd, epdconfig)
    assert epd.calls == ["prepare", "display", "sleep"]


def test_successful_refresh_sleeps(monkeypatch, image_path):
    epdconfig, epd = FakeEpdConfig(busy_value=1), FakeEpd()
    ctrl = run_with_fake_panel(monkeypatch, image_path, epd, epdconfig)
    assert epd.calls == ["prepare", "display", "sleep"]
    assert ctrl.last_error is None


PRIMARIES = {tuple(dc.PANEL_PALETTE[i : i + 3]) for i in range(0, 21, 3)}


def _driver_quantize(img):
    """What epd7in3f.getbuffer() does: quantise to the primaries with Floyd-Steinberg."""
    palette = Image.new("P", (1, 1))
    palette.putpalette(list(dc.PANEL_PALETTE) + [0, 0, 0] * 249)
    return img.quantize(palette=palette).convert("RGB")


def test_measured_mapping_sends_pure_primaries_the_driver_keeps(tmp_path):
    path = tmp_path / "grad.png"
    grad = Image.new("RGB", (64, 32))
    grad.putdata([(x * 4, y * 8, 255 - x * 4) for y in range(32) for x in range(64)])
    grad.save(path)
    out = DisplayController("omni_epd.mock").prepare_image(path)
    colours = {c for _, c in out.getcolors(maxcolors=256)}
    assert colours <= PRIMARIES
    assert _driver_quantize(out).tobytes() == out.tobytes()


def test_measured_mapping_picks_inks_by_appearance(tmp_path):
    # Pure white and black map to the white and black inks; a mid green that the ideal
    # palette would send mostly to bright green stays a mix weighted to the dull green ink.
    path = tmp_path / "blocks.png"
    img = Image.new("RGB", (60, 20), (255, 255, 255))
    img.paste((0, 0, 0), (20, 0, 40, 20))
    img.paste((92, 107, 85), (40, 0, 60, 20))
    img.save(path)
    out = DisplayController("omni_epd.mock").prepare_image(path)
    assert out.getpixel((10, 10)) == (255, 255, 255)
    assert out.getpixel((30, 10)) == (0, 0, 0)
    block = [out.getpixel((x, y)) for x in range(44, 56) for y in range(4, 16)]
    assert block.count((0, 255, 0)) > len(block) // 3


def test_driver_mapping_keeps_adjustments(image_path):
    ctrl = DisplayController("omni_epd.mock", saturation=1.0, colour_mapping="driver")
    assert ctrl.prepare_image(image_path).getpixel((0, 0)) == (200, 120, 60)


def test_invalid_colour_mapping_rejected():
    with pytest.raises(ValueError):
        DisplayController("omni_epd.mock", colour_mapping="fancy")


def test_mock_preview_uses_measured_inks(tmp_path, image_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    out = tmp_path / "mock.png"
    DisplayController("omni_epd.mock", mock_output=out).display_image(image_path)
    colours = {c for _, c in Image.open(out).convert("RGB").getcolors(maxcolors=256)}
    assert colours <= set(dc.MEASURED_INKS)
