# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Daily positive-news display for a Waveshare 7.3" 7-color ePaper screen running on a Raspberry Pi Zero 2 W. Fetches RTÉ RSS headlines, filters for positivity via OpenAI, generates a composite headline and watercolour-style artwork, and pushes it to the display. A Flask web UI allows manual regeneration and history browsing.

## Development Setup

Dependencies are managed with [uv](https://docs.astral.sh/uv/). Install uv first if needed: `curl -LsSf https://astral.sh/uv/install.sh | sh`

```bash
cp .env.example .env           # fill in OPENAI_API_KEY; EPAPER_DEVICE=omni_epd.mock for local dev
```

omni-epd is installed from GitHub (declared in `pyproject.toml`). It brings in the Waveshare driver as a dependency — no separate driver clone is needed.

#### macOS install (IT8951 C extension workaround)

IT8951 (a transitive dep of omni-epd) has a Cython extension that includes `linux/spi/spidev.h`. On macOS you must supply a stub header at compile time:

```bash
mkdir -p /tmp/spi_stub/linux/spi
cat > /tmp/spi_stub/linux/spi/spidev.h << 'EOF'
#pragma once
#include <stdint.h>
struct spi_ioc_transfer { uint64_t tx_buf, rx_buf; uint32_t len, speed_hz; uint16_t delay_usecs; uint8_t bits_per_word, cs_change, tx_nbits, rx_nbits; uint16_t pad; };
#define SPI_IOC_MAGIC 'k'
#define SPI_MSGSIZE(N) (N * sizeof(struct spi_ioc_transfer))
#define SPI_IOC_MESSAGE(N) _IOW(SPI_IOC_MAGIC, 0, char[SPI_MSGSIZE(N)])
#define SPI_IOC_RD_MODE _IOR(SPI_IOC_MAGIC, 1, uint8_t)
#define SPI_IOC_WR_MODE _IOW(SPI_IOC_MAGIC, 1, uint8_t)
#define SPI_IOC_RD_MAX_SPEED_HZ _IOR(SPI_IOC_MAGIC, 4, uint32_t)
#define SPI_IOC_WR_MAX_SPEED_HZ _IOW(SPI_IOC_MAGIC, 4, uint32_t)
EOF
CFLAGS="-I/tmp/spi_stub" uv sync
```

The `spidev`, `gpiod`, and `rpi-gpio` packages are skipped on macOS via `[tool.uv] override-dependencies` in `pyproject.toml`. omni-epd can't load on macOS (gpiod import, ARM-only `didder`), so with `EPAPER_DEVICE=omni_epd.mock` on a non-Linux host `DisplayController` bypasses omni-epd and writes `mock_output.png`: the image quantised to the 7 panel primaries with Pillow Floyd–Steinberg, which is what the Pi's driver does with the current `omni-epd.ini`.

### Linting / formatting (ruff) and tests

```bash
uv run ruff check .            # lint
uv run ruff format .           # format
OPENAI_API_KEY=test uv run pytest -q   # tests (no network or hardware)
```

### Migrating old Pi data

If you have an existing `data/` directory from the old version, copy it from the Pi then run:

```bash
uv run python migrate_data.py --source /path/to/old/data --target ./data --dry-run
uv run python migrate_data.py --source /path/to/old/data --target ./data
```

This copies PNGs (skips `.bmp` files), transforms metadata JSON to the new format, and updates the `current.*` symlinks.

## Running

```bash
# One-shot pipeline run (mock display for local dev)
EPAPER_DEVICE=omni_epd.mock uv run python main.py generate

# Combined scheduler + web UI (port 8000)
EPAPER_DEVICE=omni_epd.mock uv run python main.py serve
```

On the Pi, remove `EPAPER_DEVICE=omni_epd.mock` (or set `EPAPER_DEVICE=waveshare_epd.epd7in3f` in `.env`).

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | required | OpenAI auth |
| `EPAPER_DEVICE` | `waveshare_epd.epd7in3f` | omni-epd device string; use `omni_epd.mock` for dev |
| `EPAPER_TEXT_MODEL` | `gpt-6-luna` | OpenAI text model for positivity + headline |
| `EPAPER_IMAGE_MODEL` | `gpt-image-2.5-flare` | OpenAI image model (quality `medium` via `EPAPER_IMAGE_QUALITY`) |
| `EPAPER_IMAGE_SIZE` | `1600x960` | Requested image size (5:3, exactly 2x the panel) |
| `EPAPER_RSS_FEED_URL` | RTÉ RSS URL | News source |
| `EPAPER_DATA_DIR` | `./data` | Output root |
| `EPAPER_SCHEDULE_HOUR` / `MINUTE` | `8` / `0` | Daily run time (Europe/Dublin) |
| `EPAPER_WEB_PORT` | `8000` | Flask port |
| `EPAPER_DISPLAY_GAMMA` | `1.0` | Gamma correction applied first; < 1.0 lifts shadows (below 0.9 washes out darks) |
| `EPAPER_DISPLAY_CONTRAST` | `1.0` | PIL contrast multiplier |
| `EPAPER_DISPLAY_BRIGHTNESS` | `1.0` | PIL brightness multiplier (1.0 = no change) |
| `EPAPER_DISPLAY_SATURATION` | `1.2` | PIL colour saturation multiplier (1.0 = no change) |

## Architecture

The pipeline runs sequentially through five stages orchestrated by `pipeline.py:run_generation()`:

1. **`rss_service.py`** — fetches RSS via `feedparser`, normalises to `Article` models
2. **`positivity.py`** — sends a sample to the text model for JSON positivity scores; selects top N
3. **`headline.py`** — generates an upbeat Irish-English headline + subheading via text model
4. **`image_pipeline.py`** — calls OpenAI image API (base64 response), resizes to 800×480, saves PNG
5. **`display_controller.py`** — passes the PNG to omni-epd's `display()` method, which handles palette quantization and dithering internally using `didder`

The `serve` command wires these together: `SchedulerService` (APScheduler) fires the daily job via `JobManager`, which also handles cancellation when the web UI triggers a manual regeneration. The scheduler runs in UTC with a Europe/Dublin `CronTrigger` (APScheduler 3.10 fires an hour late on the spring DST change otherwise), uses `misfire_grace_time=None` (the Pi has no RTC, so NTP steps the clock forward after boot and wake-ups are late), and runs a catch-up check 5 minutes after startup if today's run was missed.

### Display driver

omni-epd abstracts the Waveshare hardware. The device string (`EPAPER_DEVICE`) maps to an omni-epd driver. Dithering is configured via `omni-epd.ini` in the project root. It currently has no `dither` option on purpose, so the Waveshare driver's own Pillow Floyd–Steinberg is used. omni-epd silently ignores unknown dither names (e.g. `Bayer4x4`; the valid form is `dither=Bayer` + `dither_args=4,4`), so `DisplayController.initialize()` validates the name at startup and logs an error.

The panel is powered only during a refresh: `display_image()` serialises refreshes with a lock, enforces Waveshare's 180 s minimum interval (`/push` gets a 409 instead of waiting), gives the driver's busy-wait a 90 s deadline, and always ends with `sleep()` (or `module_exit()` after a timeout). `serve` turns SIGTERM into a clean shutdown so a restart can't cut a refresh off mid-way.

### Illustration style

`epaper_goodnews/style.py` holds the house style (`STYLE_PROMPT`), the e-paper notes, and the reference preamble, applied to every image. With `EPAPER_IMAGE_STYLE_REFERENCES` on (the default), the three panels in `epaper_goodnews/style_refs/` (late-2025 `gpt-image-1-mini` images) are sent through `images.edit` as style references. This is what keeps `gpt-image-2.5-flare` from drifting towards detailed or realistic rendering. Change the style by editing `style.py` or swapping reference images, not by adding style words to the scene prompt.

### Colour mapping

With `EPAPER_DISPLAY_COLOUR_MAPPING=measured` (the default), `DisplayController` picks inks by how they actually look. It squeezes the image into the range between the black and white inks, boosts saturation by 1.4, and Floyd–Steinberg dithers against `MEASURED_INKS` (photographed 2026-10-17). It then sends the matching pure primaries, which the driver passes through unchanged. In this mode the `EPAPER_DISPLAY_GAMMA/CONTRAST/BRIGHTNESS/SATURATION` settings are not used; they apply only with `EPAPER_DISPLAY_COLOUR_MAPPING=driver`. On macOS the mock's `mock_output.png` is rendered with the measured inks, so it previews the panel.

### Storage layout

```
data/
  images/YYYY-MM-DD-<runid>.png   ← full-colour PNG (web UI + omni-epd input)
  metadata/YYYY-MM-DD-<runid>.json
  current.png   → symlink to latest PNG
  current.json  → symlink to latest metadata
logs/app.log    (rotating, 2 MB × 5)
```

No BMP files are generated — omni-epd handles the 7-colour conversion at display time.

### Cancellation

`JobManager` holds a single `threading.Event` cancel flag. `pipeline.py` calls `_check_cancel()` between each stage. The web `/generate` POST sets the flag and waits before starting a new run.
