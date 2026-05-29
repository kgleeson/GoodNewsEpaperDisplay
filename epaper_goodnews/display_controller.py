from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

from PIL import Image

LOGGER = logging.getLogger(__name__)

EPAPER_DRIVER_ENV = "EPAPER_DRIVER_PATH"

_cached_driver: Optional[object] = None


def _load_driver() -> Optional[object]:  # pragma: no cover - hardware dependency
    try:
        from waveshare_epd import epd7in3f  # type: ignore

        return epd7in3f
    except ImportError:
        driver_path = os.getenv(EPAPER_DRIVER_ENV)
        if driver_path:
            path = Path(driver_path)
            if path.exists():
                sys_path_entry = str(path)
                if sys_path_entry not in sys.path:
                    sys.path.insert(0, sys_path_entry)
                try:
                    from waveshare_epd import epd7in3f  # type: ignore

                    return epd7in3f
                except ImportError:
                    LOGGER.debug(
                        "waveshare_epd still unavailable even after adding %s to sys.path",
                        driver_path,
                    )
            else:
                LOGGER.warning("EPAPER_DRIVER_PATH %s does not exist", driver_path)
    return None


def _get_driver() -> Optional[object]:  # pragma: no cover - hardware dependency
    global _cached_driver
    if _cached_driver is None:
        _cached_driver = _load_driver()
    return _cached_driver


class DisplayController:
    def __init__(self) -> None:
        self._epd = None
        self._initialized = False

    def initialize(self, force: bool = False) -> bool:
        driver = _get_driver()
        if driver is None:
            LOGGER.warning(
                "waveshare_epd not available; set %s to the cloned Waveshare e-Paper python/lib directory",
                EPAPER_DRIVER_ENV,
            )
            return False

        if self._epd is None:
            self._epd = driver.EPD()
            force = True

        if force or not self._initialized:
            self._epd.init()
            self._initialized = True
            LOGGER.info('Initialized Waveshare 7.3" display')

        return True

    def display_image(self, image_path: Path) -> None:
        if not self.initialize(force=True):
            LOGGER.info("Display not initialized; skipping image update")
            return

        LOGGER.info("Updating ePaper display with %s", image_path)
        image = Image.open(image_path)
        try:
            self._epd.display(self._epd.getbuffer(image))
        except OSError as exc:
            LOGGER.warning(
                "Display update failed (%s); retrying after reinitialization", exc
            )
            if self.initialize(force=True):
                self._epd.display(self._epd.getbuffer(image))
            else:
                LOGGER.error("Unable to reinitialize display after failure")
                return
        finally:
            image.close()

        self._epd.sleep()
        self._initialized = False

    def sleep(self) -> None:
        if self._epd:
            self._epd.sleep()
            self._initialized = False
            LOGGER.info("Display set to sleep mode")
