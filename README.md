# Good News Display

A daily positive-news display for the **Waveshare 7.3" 7-colour ePaper** screen, running on a Raspberry Pi Zero 2 W. Each morning it fetches the latest RTÉ headlines, filters them for positivity using OpenAI, generates a composite headline and a watercolour-style illustration, dithers it to the display's 7-colour palette, and refreshes the screen. A small Flask web UI lets you view the current image, browse history, and trigger a manual regeneration.

![7-colour ePaper palette: black, red, green, blue, yellow, orange, white](.github/palette-preview.png)

---

## Hardware

| Component | Details |
|---|---|
| Display | Waveshare 7.3" e-Paper HAT (F) — `epd7in3f` |
| Host | Raspberry Pi Zero 2 W (Raspberry Pi OS Lite) |
| Resolution | 800 × 480, 7-colour (white, black, red, green, blue, yellow, orange) |
| Refresh time | ~30 s per full update |

---

## Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) for dependency management
- An OpenAI API key (for `gpt-4o-mini` text generation and `gpt-image-1` image generation)
- On the Pi: SPI enabled via `raspi-config`

---

## Setup

```bash
# 1. Clone
git clone <repo-url>
cd epaper_goodnews

# 2. Install dependencies
uv sync

# 3. Configure
cp .env.example .env
# Edit .env: add your OPENAI_API_KEY
# Set EPAPER_DEVICE=omni_epd.mock for local dev, or waveshare_epd.epd7in3f on the Pi
```

**System packages required on the Pi:**
```bash
sudo apt install libopenjp2-7 libtiff5 libjpeg62-turbo zlib1g-dev libfreetype6 liblcms2-2
```

---

## Running

```bash
# One-shot: fetch news, generate image, update display
uv run python main.py generate

# Daemon: combined daily scheduler + web UI (http://0.0.0.0:8000)
uv run python main.py serve
```

Set `EPAPER_DEVICE=omni_epd.mock` (in `.env` or as a prefix) to run the full pipeline on macOS or any machine without hardware — the mock driver saves a local JPEG for inspection instead of touching the display.

---

## Configuration

All configuration is via environment variables (`.env` file):

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | **required** | OpenAI authentication |
| `EPAPER_DEVICE` | `waveshare_epd.epd7in3f` | omni-epd device string; use `omni_epd.mock` for dev |
| `EPAPER_TEXT_MODEL` | `gpt-4o-mini` | OpenAI model for positivity scoring and headline generation |
| `EPAPER_IMAGE_MODEL` | `gpt-image-1` | OpenAI model for image generation |
| `EPAPER_IMAGE_SIZE` | `1536x1024` | Requested image size (downscaled to 800×480) |
| `EPAPER_RSS_FEED_URL` | RTÉ News RSS | Any RSS feed URL |
| `EPAPER_DATA_DIR` | `./data` | Output directory for images and metadata |
| `EPAPER_SCHEDULE_HOUR` | `8` | Hour of daily run (Europe/Dublin) |
| `EPAPER_SCHEDULE_MINUTE` | `0` | Minute of daily run |
| `EPAPER_WEB_PORT` | `8000` | Flask web server port |
| `EPAPER_POSITIVITY_SAMPLE_SIZE` | `8` | Articles sampled for positivity scoring |
| `EPAPER_POSITIVITY_SELECT_COUNT` | `3` | Top articles passed to headline/image generation |

### Dithering

Image quality for the 7-colour display is configured in `omni-epd.ini`:

```ini
[Display]
dither=FloydSteinberg
dither_strength=1.0
dither_serpentine=True
```

Available algorithms: `FloydSteinberg`, `Bayer`, `Atkinson`, `Sierra`. See the [omni-epd dithering docs](https://github.com/robweber/omni-epd/wiki/Image-Dithering-Options).

---

## How it works

The pipeline runs once per day (or on demand via the web UI) through five sequential stages:

```
RSS Feed → Positivity Filter → Headline → Image → Display
```

1. **RSS** — `feedparser` pulls the configured feed and normalises articles into typed models
2. **Positivity filter** — a sample of articles is scored 0–1 by `gpt-4o-mini`; the top N are selected
3. **Headline** — the selected articles are summarised into a short upbeat Irish-English headline and subheading
4. **Image** — `gpt-image-1` generates a watercolour illustration based on the headline and article themes; the response is decoded, resized to 800×480, and saved as a PNG
5. **Display** — the PNG is passed to [omni-epd](https://github.com/robweber/omni-epd), which handles 7-colour quantization and dithering internally before pushing to the hardware

Each run produces:
```
data/
  images/YYYY-MM-DD-<runid>.png    ← full-colour PNG
  metadata/YYYY-MM-DD-<runid>.json ← prompts, articles, models used
  current.png                      → symlink to latest PNG
  current.json                     → symlink to latest metadata
logs/app.log
```

---

## Web UI

The `serve` command starts the scheduler and a Flask web server at port 8000:

| Route | Description |
|---|---|
| `/` | Current edition: headline, image, status |
| `/history` | Paginated archive with thumbnails |
| `POST /generate` | Trigger immediate regeneration |
| `/api/current` | JSON: current metadata + job status |
| `/api/history` | JSON: recent run history |
| `/health` | JSON: last run/success status |

---

## Systemd deployment

Create `/etc/systemd/system/epaper.service`:

```ini
[Unit]
Description=Good News Display
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/home/pi/epaper_goodnews
ExecStart=/home/pi/epaper_goodnews/.venv/bin/python main.py serve
Restart=on-failure
RestartSec=10
User=pi
EnvironmentFile=/home/pi/epaper_goodnews/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable epaper
sudo systemctl start epaper
```

---

## Migrating old data

If you have images and metadata from a previous version of this project, copy them from the Pi then run:

```bash
# Dry run first
uv run python migrate_data.py --source ./old-data --target ./data --dry-run

# Apply
uv run python migrate_data.py --source ./old-data --target ./data
```

The script copies PNGs (skips `.bmp` files), transforms metadata to the current format, and updates the `current.*` symlinks.

---

## Development

```bash
uv run ruff check .      # lint
uv run ruff format .     # format
```

To test the full pipeline without hardware:

```bash
EPAPER_DEVICE=omni_epd.mock uv run python main.py generate
```

The mock driver writes a JPEG to the working directory so you can inspect the dithered output locally.
