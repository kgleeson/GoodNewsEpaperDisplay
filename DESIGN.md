# E-Paper Good News Display Design

## Overview
- Daily positive-news display for Waveshare 7.3" 7-color ePaper
- Runs on Raspberry Pi Zero 2 W with Raspberry Pi OS Lite
- Integrates RTÉ RSS feed, OpenAI-powered headline/image generation, Flask web UI

## Hardware & Display
- Waveshare `epd7in3f` driver from `waveshare/e-Paper`
- Render 800×480 canvas, quantize to 7-color palette, save PNG/BMP artifacts
- Daily refresh pushes new image; fallback to previous if generation fails

## Scheduling & Runtime
- Systemd-managed daemon with internal scheduler (Europe/Dublin timezone)
- Daily job respects DST using `zoneinfo`
- Manual regenerate endpoint cancels in-flight job before rerun

## Failure Handling
- No offline fallback; failures abort job and leave last image active
- Errors logged; manual triggers report failure state
- Display remains on previous image on error

## Data Flow
1. **RSS Fetching**: `feedparser` pulls RTÉ feed, normalizes title, description, pubDate, link
2. **Positivity Filter**: OpenAI text model scores positivity, top entries selected
3. **Headline Generation**: OpenAI creates upbeat composite headline and subheading
4. **Image Synthesis**: OpenAI image API creates text-free artwork with palette hints (requesting an API-supported size and resizing to 800×480); a full-colour PNG is kept for the web UI while a 7-colour Floyd–Steinberg dithered BMP is generated for the display
5. **Display Output**: Convert to BMP, refresh ePaper, enter sleep mode
- Prompts, responses, article details stored for traceability

## Storage Layout
- `data/images/YYYY-MM-DD.{png,bmp}` for web/display files
- `data/metadata/YYYY-MM-DD.json` with run metadata, prompts, selected articles
- `data/current.{png,json}` symlinks to latest assets
- Rotating logs in `logs/app.log`
- No automatic pruning; monitor storage manually

## Application Structure
- `main.py` CLI (`generate`, `display`, `serve`, `run-scheduled`)
- Modules: `config`, `rss_service`, `positivity`, `headline`, `image_pipeline`, `storage`, `display_controller`, `scheduler`, `web/app`, `models`
- Synchronous flow acceptable; optional `asyncio`
- CLI options support headless runs (`--skip-display`) for local development on macOS

## Flask Web Server
- Port 8000 (configurable)
- Routes: `/`, `/history`, `/generate` (POST), `/api/current`, `/api/history`
- Background job manager handles manual regen cancellations
- Templates localized for English (Ireland); static assets via symlinks

## Concurrency & Cancellation
- Central `JobManager` with cancel flag and single worker
- Manual regen sets cancel flag, waits for safe checkpoints
- Scheduler leverages same job manager to avoid overlap

## OpenAI Integration
- Use official Python SDK with `OPENAI_API_KEY`
- Positivity scoring returns structured JSON for deterministic parsing using `gpt-5-mini`
- Headline generation also uses `gpt-5-mini` for consistent tone
- Image synthesis uses `gpt-image-1` with palette hints suited to 7-color output
- Limit retries to one; log token usage, model names, response IDs

## Testing Strategy
- `pytest` unit tests: RSS parsing, positivity prompt builder, palette conversion, job cancellation
- Integration tests with mocked OpenAI and RSS responses
- Hardware bypass mode for development

## Deployment & Ops
- System packages: `python3-pip`, `python3-venv`, `libopenjp2-7`, `libtiff5`, `libjpeg62-turbo`, `zlib1g-dev`, `libfreetype6`, `liblcms2-2`
- Python deps pinned in `requirements.txt`
- Systemd unit `epaper.service` for daemon; timer optional
- `/health` endpoint exposes last job status
- Secrets passed via `.env` file (e.g., `OPENAI_API_KEY`) with `.env.example` template
- OpenAI image requests default to `1536x1024` (landscape) per latest `gpt-image-1` guidance before downscaling to 800×480
- Provide Waveshare driver via cloned `e-Paper` repo; set `EPAPER_DRIVER_PATH` to its `python/lib` directory for Python 3.13 compatibility

## Localization
- Dates formatted using `zoneinfo.ZoneInfo("Europe/Dublin")` and locale `en_IE`
- Headline text enforced English tone in prompts

## Risks & Considerations
- Pi Zero 2 performance when processing large images; optimize Pillow pipeline
- ePaper refresh duration (~15s); ensure single refresh per run
- Storage growth due to unlimited history; provide monitoring script

## Decision Log
1. Run as single-process daemon combining scheduler and web server
2. Use 800×480 image resolution when calling `gpt-image-1`
