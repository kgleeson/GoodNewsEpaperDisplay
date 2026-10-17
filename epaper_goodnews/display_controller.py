from __future__ import annotations

import configparser
import logging
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageEnhance

LOGGER = logging.getLogger(__name__)

# Waveshare recommends at least 180 s between refreshes of the colour panels.
MIN_REFRESH_INTERVAL_SECONDS = 180
# A normal 7-colour refresh takes ~35 s in total; each busy-wait gets this long.
BUSY_TIMEOUT_SECONDS = 90

MOCK_DEVICE = "omni_epd.mock"
PANEL_PALETTE = (0, 0, 0, 255, 255, 255, 0, 255, 0, 0, 0, 255, 255, 0, 0, 255, 255, 0, 255, 128, 0)

# Dither names omni-epd 0.4.2 understands (virtualepd.py), after it lowercases them and
# strips hyphens. Anything else is silently ignored by omni-epd.
VALID_DITHERS = {
    "none",
    "bayer",
    "random",
    "customordered",
    "customdiffusion",
    "clustereddot4x4",
    "clustereddotdiagonal8x8",
    "vertical5x3",
    "horizontal3x5",
    "clustereddotdiagonal6x6",
    "clustereddotdiagonal8x8_2",
    "clustereddotdiagonal16x16",
    "clustereddot6x6",
    "clustereddotspiral5x5",
    "clustereddothorizontalline",
    "clustereddotverticalline",
    "clustereddot8x8",
    "clustereddot6x6_2",
    "clustereddot6x6_3",
    "clustereddotdiagonal8x8_3",
    "simple2d",
    "floydsteinberg",
    "falsefloydsteinberg",
    "jarvisjudiceninke",
    "atkinson",
    "stucki",
    "burkes",
    "sierra",
    "tworowsierra",
    "sierralite",
    "stevenpigeon",
    "sierra3",
    "sierra2",
    "sierra2_4a",
}

_GAMMA_LUT_CACHE: dict[float, list[int]] = {}


class DisplayBusyError(RuntimeError):
    pass


class PanelTimeoutError(RuntimeError):
    pass


def _gamma_lut(gamma: float) -> list[int]:
    if gamma not in _GAMMA_LUT_CACHE:
        _GAMMA_LUT_CACHE[gamma] = [int(255 * (i / 255) ** gamma) for i in range(256)]
    return _GAMMA_LUT_CACHE[gamma]


def check_dither_config(ini_path: Path) -> str | None:
    """Return an error message if omni-epd.ini names a dither omni-epd won't recognise."""
    if not ini_path.exists():
        return None
    parser = configparser.ConfigParser()
    parser.read(ini_path)
    raw = parser.get("Display", "dither", fallback="").strip()
    if not raw:
        return None
    # Same normalisation as omni-epd's VirtualEPD.__applyConfig.
    name = raw.lower().replace("sierra-2-4a", "sierralite").replace("-", "")
    if name not in VALID_DITHERS:
        return (
            f"omni-epd.ini dither={raw!r} is not a valid omni-epd dither name; "
            "omni-epd will ignore it (for Bayer 4x4 use dither=Bayer with dither_args=4,4)"
        )
    return None


class DisplayController:
    def __init__(
        self,
        device_type: str,
        saturation: float = 1.2,
        brightness: float = 1.0,
        contrast: float = 1.0,
        gamma: float = 1.0,
        ini_path: Path = Path("omni-epd.ini"),
        mock_output: Path = Path("mock_output.png"),
    ) -> None:
        self._device_type = device_type
        self._saturation = saturation
        self._brightness = brightness
        self._contrast = contrast
        self._gamma = gamma
        self._ini_path = ini_path
        self._mock_output = mock_output
        self._lock = threading.Lock()
        self._last_refresh_monotonic: float | None = None
        self._driver_used = False
        self._closing = False
        self.last_refresh_at: datetime | None = None
        self.last_error: str | None = None

    def initialize(self) -> bool:
        """Check configuration without touching the panel.

        The panel is only powered for the duration of each refresh.
        """
        error = check_dither_config(self._ini_path)
        if error:
            LOGGER.error(error)
            self.last_error = error
            return False
        return True

    def prepare_image(self, image_path: Path) -> Image.Image:
        with Image.open(image_path) as src:
            img = src.convert("RGB")
        if self._gamma != 1.0:
            img = img.point(_gamma_lut(self._gamma) * 3)
        if self._contrast != 1.0:
            img = ImageEnhance.Contrast(img).enhance(self._contrast)
        if self._brightness != 1.0:
            img = ImageEnhance.Brightness(img).enhance(self._brightness)
        if self._saturation != 1.0:
            img = ImageEnhance.Color(img).enhance(self._saturation)
        return img

    def display_image(self, image_path: Path, *, wait: bool = True) -> None:
        """Refresh the panel with `image_path`.

        With wait=True (scheduled runs) this queues behind any refresh in progress and
        waits out the minimum refresh interval. With wait=False (manual pushes) it
        raises DisplayBusyError instead of waiting.
        """
        if not self._lock.acquire(blocking=wait):
            raise DisplayBusyError("Display is busy with another refresh")
        try:
            if self._closing:
                raise DisplayBusyError("Display is shutting down")
            remaining = self._seconds_until_refresh_allowed()
            if remaining > 0:
                if not wait:
                    raise DisplayBusyError(
                        f"Last refresh was too recent; try again in {int(remaining) + 1} s"
                    )
                LOGGER.info("Waiting %.0f s before refreshing the panel", remaining)
                time.sleep(remaining)
            self._refresh(image_path)
        except Exception as exc:
            self.last_error = f"{datetime.now().isoformat(timespec='seconds')}: {exc}"
            LOGGER.error("Display update failed: %s", exc)
            raise
        finally:
            self._lock.release()

    def _seconds_until_refresh_allowed(self) -> float:
        if self._last_refresh_monotonic is None:
            return 0.0
        elapsed = time.monotonic() - self._last_refresh_monotonic
        return max(0.0, MIN_REFRESH_INTERVAL_SECONDS - elapsed)

    def _refresh(self, image_path: Path) -> None:
        LOGGER.info("Updating display with %s", image_path)
        # Image work happens before the panel is powered, to keep the awake window short.
        img = self.prepare_image(image_path)
        if self._device_type == MOCK_DEVICE and sys.platform != "linux":
            self._write_mock_preview(img)
        else:
            self._refresh_panel(img)
        self._last_refresh_monotonic = time.monotonic()
        self.last_refresh_at = datetime.now()
        self.last_error = None

    def _write_mock_preview(self, img: Image.Image) -> None:
        # omni-epd can't load on macOS (gpiod, ARM-only didder). Approximate what the
        # Waveshare driver does: quantise to the 7 primaries with Floyd-Steinberg.
        palette = Image.new("P", (1, 1))
        palette.putpalette(PANEL_PALETTE + (0, 0, 0) * 249)
        img.quantize(palette=palette).convert("RGB").save(self._mock_output)
        LOGGER.info("Mock display: wrote preview to %s", self._mock_output)

    def _refresh_panel(self, img: Image.Image) -> None:
        from omni_epd import displayfactory  # type: ignore

        epd = displayfactory.load_display_driver(self._device_type)
        self._driver_used = True
        _install_busy_timeout(epd, BUSY_TIMEOUT_SECONDS)
        timed_out = False
        try:
            epd.prepare()
            epd.display(img)
        except PanelTimeoutError:
            timed_out = True
            raise
        finally:
            if timed_out:
                # Datasheet: no commands while BUSY is low. Cut power and close SPI instead.
                _module_exit()
            else:
                try:
                    epd.sleep()
                except Exception:
                    LOGGER.exception("Panel sleep failed; powering the module off")
                    _module_exit()
        LOGGER.info("Display updated and set to sleep")

    def close(self, timeout: float = 120) -> None:
        """Wait for any refresh in progress, then stop further refreshes."""
        acquired = self._lock.acquire(timeout=timeout)
        if not acquired:
            LOGGER.warning("Display still busy after %.0f s; closing anyway", timeout)
        try:
            self._closing = True
            if self._driver_used:
                _module_exit()
        finally:
            if acquired:
                self._lock.release()


def _install_busy_timeout(epd, timeout: float) -> None:
    """Give the Waveshare driver's busy-wait a deadline.

    The stock ReadBusyH loops forever, so a disconnected or faulty panel would hang
    the job (and keep the panel powered).
    """
    device = getattr(epd, "_device", None)
    epdconfig = sys.modules.get("waveshare_epd.epdconfig")
    if device is None or epdconfig is None or not hasattr(device, "busy_pin"):
        return

    def read_busy_with_deadline() -> None:
        deadline = time.monotonic() + timeout
        while epdconfig.digital_read(device.busy_pin) == 0:  # 0: busy, 1: idle
            if time.monotonic() > deadline:
                raise PanelTimeoutError(f"Panel still busy after {timeout:.0f} s")
            epdconfig.delay_ms(5)

    device.ReadBusyH = read_busy_with_deadline


def _module_exit() -> None:
    epdconfig = sys.modules.get("waveshare_epd.epdconfig")
    if epdconfig is None:
        return
    try:
        epdconfig.module_exit()
    except Exception:
        LOGGER.exception("Failed to power off the display module")
