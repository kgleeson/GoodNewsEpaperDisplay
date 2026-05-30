from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageEnhance

LOGGER = logging.getLogger(__name__)


class DisplayController:
    def __init__(self, device_type: str, saturation: float = 1.2, brightness: float = 1.3) -> None:
        self._device_type = device_type
        self._saturation = saturation
        self._brightness = brightness
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
        LOGGER.info("Updating display with %s", image_path)
        try:
            from omni_epd import displayfactory  # type: ignore
            epd = displayfactory.load_display_driver(self._device_type)
            epd.prepare()
            img = Image.open(image_path).convert("RGB")
            if self._brightness != 1.0:
                img = ImageEnhance.Brightness(img).enhance(self._brightness)
            if self._saturation != 1.0:
                img = ImageEnhance.Color(img).enhance(self._saturation)
            epd.display(img)
            epd.sleep()
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
