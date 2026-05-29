from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

LOGGER = logging.getLogger(__name__)


class DisplayController:
    def __init__(self, device_type: str) -> None:
        self._device_type = device_type
        self._epd = None

    def initialize(self) -> bool:
        try:
            from omni_epd import displayfactory  # type: ignore
            self._epd = displayfactory.load_display_driver(self._device_type)
            result = self._epd.prepare()
            LOGGER.info("Initialized display: %s", self._device_type)
            return result
        except Exception as exc:
            LOGGER.warning("Failed to initialize display %s: %s", self._device_type, exc)
            return False

    def display_image(self, image_path: Path) -> None:
        if self._epd is None and not self.initialize():
            LOGGER.error("Display not available; skipping image update")
            return

        LOGGER.info("Updating display with %s", image_path)
        try:
            img = Image.open(image_path).convert("RGB")
            self._epd.display(img)
            self._epd.sleep()
            img.close()
            LOGGER.info("Display updated and set to sleep")
        except Exception as exc:
            LOGGER.error("Display update failed: %s", exc)
            raise

    def close(self) -> None:
        if self._epd is not None:
            try:
                self._epd.close()
            except Exception as exc:
                LOGGER.warning("Error closing display: %s", exc)
            finally:
                self._epd = None
