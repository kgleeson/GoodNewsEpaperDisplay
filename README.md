# Good News Display

A daily positive-news picture for the **Waveshare 7.3" 7-colour ePaper** screen, running on a Raspberry Pi Zero 2 W. Each morning it fetches the latest RTÉ headlines, scores them for positivity with OpenAI, writes a headline, generates a watercolour-style illustration of the best stories and refreshes the screen. The panel shows the image only; the headline appears in a small Flask web UI, which also lets you browse past editions, push one back to the screen and trigger a new run.

---

## Hardware

| Component | Details |
|---|---|
| Display | Waveshare 7.3" e-Paper HAT (F) — `epd7in3f` |
| Host | Raspberry Pi Zero 2 W (Raspberry Pi OS Lite) |
| Resolution | 800 × 480, 7-colour (black, white, green, blue, red, yellow, orange) |
| Refresh time | ~35 s per full update; at least 180 s between refreshes |

---

## Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) for dependency management
- An OpenAI API key
- On the Pi: SPI enabled via `raspi-config`

---

## Setup

```bash
git clone <repo-url>
cd epaper_goodnews
uv sync
cp .env.example .env    # add your OPENAI_API_KEY
```

Set `EPAPER_DEVICE=omni_epd.mock` in `.env` for local development, or `waveshare_epd.epd7in3f` on the Pi.

**System packages required on the Pi:**

```bash
sudo apt install libopenjp2-7 libtiff5 libjpeg62-turbo zlib1g-dev libfreetype6 liblcms2-2
```

**macOS:** one of omni-epd's dependencies has a C extension that needs a Linux SPI header. `CLAUDE.md` has the stub header and the `CFLAGS=... uv sync` command to get past it.

---

## Running

```bash
# One-shot: fetch news, generate the image, update the display
uv run python main.py generate

# Daemon: daily scheduler + web UI (http://0.0.0.0:8000)
uv run python main.py serve
```

`generate` exits with status 1 if the run fails, and 0 for a success or a no-good-news skip.

With `EPAPER_DEVICE=omni_epd.mock` on a machine that isn't Linux, no hardware is touched: the display step writes `mock_output.png`, a preview of how the panel's inks will render the image.

---

## Configuration

All configuration is through environment variables (the `.env` file). `.env.example` lists every option with notes.

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | **required** | OpenAI authentication |
| `EPAPER_DEVICE` | `waveshare_epd.epd7in3f` | omni-epd device string; `omni_epd.mock` for development |
| `EPAPER_TEXT_MODEL` | `gpt-6-luna` | Model for positivity scoring and the headline |
| `EPAPER_IMAGE_MODEL` | `gpt-image-2.5-flare` | Model for the illustration |
| `EPAPER_IMAGE_SIZE` | `1600x960` | Requested image size (5:3, twice the panel) |
| `EPAPER_IMAGE_QUALITY` | `medium` | Image quality setting |
| `EPAPER_IMAGE_STYLE_REFERENCES` | `true` | Send the bundled style reference images with each request |
| `EPAPER_RSS_FEED_URL` | RTÉ News RSS | Any RSS feed URL |
| `EPAPER_POSITIVITY_MIN_SCORE` | `0.6` | Minimum positivity score (0–1) for a story to be used |
| `EPAPER_POSITIVITY_SELECT_COUNT` | `3` | How many of the passing stories feed the headline and image |
| `EPAPER_DATA_DIR` | `./data` | Output directory for images and metadata |
| `EPAPER_SCHEDULE_HOUR` / `EPAPER_SCHEDULE_MINUTE` | `8` / `0` | Daily run time |
| `EPAPER_TIMEZONE` | `Europe/Dublin` | Timezone of the schedule and of recorded run times |
| `EPAPER_WEB_HOST` / `EPAPER_WEB_PORT` | `0.0.0.0` / `8000` | Web server address |
| `EPAPER_DISPLAY_COLOUR_MAPPING` | `measured` | `measured` picks inks by how they really look; `driver` leaves it to the driver |
| `EPAPER_DISPLAY_PAPER_WHITE` | `true` | With `measured` mapping, treat the illustration's ivory paper as the panel's white |
| `EPAPER_DISPLAY_GAMMA` / `CONTRAST` / `BRIGHTNESS` / `SATURATION` | `1.0` / `1.0` / `1.0` / `1.2` | Image adjustments, used only with `driver` colour mapping |

The web UI has no login. It is meant for a home network only; every "Generate" click costs an image generation.

### Colour and dithering

The panel's inks are much duller than the pure colours the driver works with. In the default `measured` mode the app chooses inks itself: it squeezes the image into the range between the real black and white inks, boosts saturation, and Floyd–Steinberg dithers against measured ink colours (`MEASURED_INKS` in `display_controller.py`). It then hands the driver pure primaries, which pass through unchanged.

The illustrations are painted on ivory paper. Shown faithfully, that needs yellow ink in every light area, which gives the whole panel a khaki cast. So by default (`EPAPER_DISPLAY_PAPER_WHITE=true`) each image is rebalanced so its paper border becomes the panel's white: the border, white clothing and highlights come out clean, and the picture reads a little cooler. Images without a light paper border are left as they are. Set it to `false` for the warmer, faithful rendering.

`omni-epd.ini` deliberately has **no** `dither` option, so omni-epd adds no dithering of its own. If you do set one, note that omni-epd silently ignores names it doesn't know (Bayer 4×4 is `dither=Bayer` with `dither_args=4,4`, not `Bayer4x4`); the app checks the name at startup and logs an error.

### Illustration style

`epaper_goodnews/style.py` holds the house style and the notes about the panel's limits. The images in `epaper_goodnews/style_refs/` are sent as style references. Change the look there, not in the scene prompt.

---

## How it works

```
RSS feed → positivity scores → headline → image → display
```

1. **RSS** — the feed is downloaded (with retries) and normalised into articles.
2. **Positivity** — every article is scored 0–1 by the text model. Up to `EPAPER_POSITIVITY_SELECT_COUNT` stories scoring at least `EPAPER_POSITIVITY_MIN_SCORE` are selected.
3. **Headline** — a short, upbeat Irish-English headline and subheading for the web UI.
4. **Image** — the image model paints the most concrete story in the house style; the result is cropped and resized to 800×480 and saved as a PNG.
5. **Display** — the image is mapped to the panel's inks and the screen is refreshed, then put to sleep.

If no story passes the threshold, the run is recorded as **skipped**: no image is generated and the current image is shown again (Waveshare recommends a refresh at least every 24 hours).

Reliability behaviour of `serve`:

- A scheduled run that fails before producing an image is retried after 30 minutes and again 2 hours later.
- Five minutes after startup, a catch-up run starts if today's scheduled time has passed with no success or skip since.
- Starting a run while another is in progress cancels the first; the new one starts once the old one has stopped.
- The panel is powered only during a refresh. A refresh that has started always finishes, including on shutdown.

Each run produces:

```
data/
  images/YYYY-MM-DD-<runid>.png     full-colour 800×480 PNG
  metadata/YYYY-MM-DD-<runid>.json  articles, scores, prompts, models, status
  current.png                       symlink to the image on show
  current.json                      symlink to its metadata
logs/app.log                        rotating, 2 MB × 5
```

History is never pruned (roughly 0.7 MB per day).

---

## Web UI

| Route | Description |
|---|---|
| `/` | Current edition: headline, image, status, "Generate" button |
| `/history` | Paginated archive, with "Push to display" for past images |
| `POST /generate` | Start a run now (cancels one in progress) |
| `POST /push/<name>` | Show a past image on the panel; 409 if the panel is busy or was refreshed under 180 s ago |
| `/api/current` | JSON for the image on show, plus `job_running`, `next_run` and `last_run` (the latest run, which may have failed or been skipped) |
| `/api/history?limit=N` | JSON for recent runs |
| `/health` | JSON: `last_run`, `last_status`, `last_success`, `last_ok` (latest success or skip), `display_error`, `next_run` |

For alerting (for example a Home Assistant REST sensor), watch `/health`: `last_status` for a failed run and the age of `last_ok` for a stalled service.

---

## Systemd deployment

Create `/etc/systemd/system/epaper.service` (adjust the user and paths):

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

On `systemctl stop` or `restart` the service waits up to 75 s for a refresh in progress, which fits inside systemd's default 90 s stop timeout. Don't lower `TimeoutStopSec` below that.

---

## Migrating old data

If you have images and metadata from the first version of this project, copy them from the Pi and run:

```bash
uv run python migrate_data.py --source ./old-data --target ./data --dry-run
uv run python migrate_data.py --source ./old-data --target ./data
```

The script copies PNGs (skipping `.bmp` files), converts metadata to the current format and updates the `current.*` symlinks.

---

## Development

```bash
uv run ruff check .                    # lint
uv run ruff format .                   # format
OPENAI_API_KEY=test uv run pytest -q   # tests (no network or hardware)
```

To run the whole pipeline without hardware (this calls OpenAI and costs a little):

```bash
EPAPER_DEVICE=omni_epd.mock uv run python main.py generate
```

`test_pattern.py` draws a colour calibration pattern and can push it to the panel.
