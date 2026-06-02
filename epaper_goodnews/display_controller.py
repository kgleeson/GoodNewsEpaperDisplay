from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageEnhance

LOGGER = logging.getLogger(__name__)

_GAMMA_LUT_CACHE: dict[float, list[int]] = {}


def _gamma_lut(gamma: float) -> list[int]:
    if gamma not in _GAMMA_LUT_CACHE:
        _GAMMA_LUT_CACHE[gamma] = [int(255 * (i / 255) ** gamma) for i in range(256)]
    return _GAMMA_LUT_CACHE[gamma]


class DisplayController:
    def __init__(
        self,
        device_type: str,
        saturation: float = 1.1,
        brightness: float = 1.2,
        contrast: float = 0.9,
        gamma: float = 0.8,
    ) -> None:
        self._device_type = device_type
        self._saturation = saturation
        self._brightness = brightness
        self._contrast = contrast
        self._gamma = gamma
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
            if self._gamma != 1.0:
                lut = _gamma_lut(self._gamma) * 3
                img = img.point(lut)
            if self._contrast != 1.0:
                img = ImageEnhance.Contrast(img).enhance(self._contrast)
            if self._brightness != 1.0:
                img = ImageEnhance.Brightness(img).enhance(self._brightness)
            if self._saturation != 1.0:
                img = ImageEnhance.Color(img).enhance(self._saturation)
            # Subtle cool shift: display's yellow/orange inks skew olive/brown,
            # nudging warm tones slightly toward blue before quantization reduces that cast.
            r, g, b = img.split()
            r = r.point([int(i * 0.95) for i in range(256)])
            b = b.point([min(255, int(i * 1.05)) for i in range(256)])
            img = Image.merge("RGB", (r, g, b))
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
