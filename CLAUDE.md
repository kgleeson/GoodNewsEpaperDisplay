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

The `spidev`, `gpiod`, and `rpi-gpio` packages are skipped on macOS via `[tool.uv] override-dependencies` in `pyproject.toml`. The `gpiod` warning on display init at runtime is harmless — the mock device handles it gracefully.

### Linting / formatting (ruff)

```bash
uv run ruff check .            # lint
uv run ruff format .           # format
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
| `EPAPER_TEXT_MODEL` | `gpt-4o-mini` | OpenAI text model for positivity + headline |
| `EPAPER_IMAGE_MODEL` | `gpt-image-1` | OpenAI image model |
| `EPAPER_IMAGE_SIZE` | `1536x1024` | Requested image size (landscape) |
| `EPAPER_RSS_FEED_URL` | RTÉ RSS URL | News source |
| `EPAPER_DATA_DIR` | `./data` | Output root |
| `EPAPER_SCHEDULE_HOUR` / `MINUTE` | `8` / `0` | Daily run time (Europe/Dublin) |
| `EPAPER_WEB_PORT` | `8000` | Flask port |
| `EPAPER_DISPLAY_BRIGHTNESS` | `1.3` | PIL brightness multiplier applied before display (1.0 = no change) |
| `EPAPER_DISPLAY_SATURATION` | `1.2` | PIL colour saturation multiplier applied before display (1.0 = no change) |

## Architecture

The pipeline runs sequentially through five stages orchestrated by `pipeline.py:run_generation()`:

1. **`rss_service.py`** — fetches RSS via `feedparser`, normalises to `Article` models
2. **`positivity.py`** — sends a sample to the text model for JSON positivity scores; selects top N
3. **`headline.py`** — generates an upbeat Irish-English headline + subheading via text model
4. **`image_pipeline.py`** — calls OpenAI image API (base64 response), resizes to 800×480, saves PNG
5. **`display_controller.py`** — passes the PNG to omni-epd's `display()` method, which handles palette quantization and dithering internally using `didder`

The `serve` command wires these together: `SchedulerService` (APScheduler, DST-aware) fires the daily job via `JobManager`, which also handles cancellation when the web UI triggers a manual regeneration.

### Display driver

omni-epd abstracts the Waveshare hardware. The device string (`EPAPER_DEVICE`) maps to an omni-epd driver. Dithering is configured via `omni-epd.ini` in the project root — adjust `dither`, `dither_strength`, and `dither_serpentine` to tune image quality.

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
