"""
Generate and optionally display a colour-test pattern for the 7-colour e-paper screen.

Usage:
    # Save pattern PNG only (no display hardware needed)
    uv run python test_pattern.py

    # Save AND push to display (Pi only)
    uv run python test_pattern.py --display

    # Apply the same display adjustments the pipeline uses
    uv run python test_pattern.py --display --adjust
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 800, 480

# The 7 colours omni-epd maps to on this display (from omni-epd.ini palette_filter)
PALETTE = [
    ("Black",   (0,   0,   0)),
    ("White",   (255, 255, 255)),
    ("Green",   (0,   255, 0)),
    ("Blue",    (0,   0,   255)),
    ("Red",     (255, 0,   0)),
    ("Yellow",  (255, 255, 0)),
    ("Orange",  (255, 128, 0)),
]

# Typical real-world colours to show how they quantize
REAL_COLOURS = [
    ("Sky",     (100, 180, 240)),
    ("Grass",   (60,  160, 60)),
    ("Skin",    (230, 170, 110)),
    ("Sky2",    (70,  140, 210)),
    ("Teal",    (0,   180, 160)),
    ("Brown",   (130, 80,  40)),
]

try:
    _font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 11)
    _font_sm = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 9)
except Exception:
    _font = ImageFont.load_default()
    _font_sm = _font


def _label_colour(bg: tuple[int, int, int]) -> tuple[int, int, int]:
    r, g, b = bg
    return (0, 0, 0) if (0.299 * r + 0.587 * g + 0.114 * b) > 128 else (255, 255, 255)


def _draw_text_centred(draw: ImageDraw.ImageDraw, text: str, cx: int, cy: int,
                        fill: tuple[int, int, int], font: ImageFont.FreeTypeFont) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((cx - tw // 2, cy - th // 2), text, fill=fill, font=font)


def build_pattern() -> Image.Image:
    img = Image.new("RGB", (W, H), (240, 240, 240))
    draw = ImageDraw.Draw(img)

    # ── Section 1: solid palette swatches (top strip, ~70px tall) ─────────────
    swatch_h = 68
    swatch_w = W // len(PALETTE)
    for i, (name, colour) in enumerate(PALETTE):
        x0 = i * swatch_w
        draw.rectangle([x0, 0, x0 + swatch_w - 1, swatch_h - 1], fill=colour)
        draw.rectangle([x0, 0, x0 + swatch_w - 1, swatch_h - 1], outline=(80, 80, 80))
        _draw_text_centred(draw, name, x0 + swatch_w // 2, swatch_h // 2,
                           _label_colour(colour), _font)

    # ── Section 2: gradient ramps white → each palette colour (middle block) ──
    ramp_top = swatch_h + 4
    ramp_h = 90
    steps = W
    for i, (name, colour) in enumerate(PALETTE):
        row_y = ramp_top + i * (ramp_h // len(PALETTE))
        row_h = ramp_h // len(PALETTE) - 1
        for x in range(steps):
            t = x / (steps - 1)
            r = int(255 + (colour[0] - 255) * t)
            g = int(255 + (colour[1] - 255) * t)
            b = int(255 + (colour[2] - 255) * t)
            draw.line([(x, row_y), (x, row_y + row_h - 1)], fill=(r, g, b))
        label_fg = _label_colour(colour)
        draw.text((4, row_y + 1), f"White→{name}", fill=(0, 0, 0), font=_font_sm)

    # ── Section 3: "real world" colour swatches + their neighbours ─────────────
    real_top = ramp_top + ramp_h + 4
    real_h = (H - real_top - 4) // 2
    real_w = W // len(REAL_COLOURS)
    for i, (name, colour) in enumerate(REAL_COLOURS):
        x0 = i * real_w
        # Top half: raw colour
        draw.rectangle([x0, real_top, x0 + real_w - 1, real_top + real_h - 1], fill=colour)
        _draw_text_centred(draw, name, x0 + real_w // 2, real_top + real_h // 2,
                           _label_colour(colour), _font)
        # Bottom half: slightly darker version (-40)
        darker = tuple(max(0, c - 60) for c in colour)
        draw.rectangle([x0, real_top + real_h, x0 + real_w - 1, H - 2], fill=darker)  # type: ignore[arg-type]
        _draw_text_centred(draw, "dark", x0 + real_w // 2, real_top + real_h + (H - real_top - real_h) // 2,
                           _label_colour(darker), _font_sm)  # type: ignore[arg-type]

    # border
    draw.rectangle([0, 0, W - 1, H - 1], outline=(0, 0, 0))

    return img


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--display", action="store_true", help="push to e-paper display")
    parser.add_argument("--adjust", action="store_true",
                        help="apply display adjustments (gamma/contrast/brightness/saturation)")
    parser.add_argument("--out", default="test_pattern.png", help="output PNG path")
    args = parser.parse_args()

    img = build_pattern()
    out = Path(args.out)
    img.save(out)
    print(f"Saved: {out}")

    if args.display:
        import os
        from epaper_goodnews.config import load_config
        from epaper_goodnews.display_controller import DisplayController

        cfg = load_config()
        device = os.getenv("EPAPER_DEVICE", cfg.device_type)

        if args.adjust:
            ctrl = DisplayController(
                device_type=device,
                saturation=cfg.display_saturation,
                brightness=cfg.display_brightness,
                contrast=cfg.display_contrast,
                gamma=cfg.display_gamma,
            )
            print(f"Adjustments: gamma={cfg.display_gamma} contrast={cfg.display_contrast} "
                  f"brightness={cfg.display_brightness} saturation={cfg.display_saturation}")
        else:
            ctrl = DisplayController(device_type=device)
            print("No display adjustments (raw output)")

        print(f"Pushing to display: {device}")
        ctrl.display_image(out)
        print("Done.")


if __name__ == "__main__":
    main()
