# Good News Display: design

How the project is put together and why. `README.md` covers setup and use; `CLAUDE.md` has the detailed working notes.

## Purpose

- One cheerful, news-based picture a day on a Waveshare 7.3" 7-colour ePaper panel.
- Runs unattended on a Raspberry Pi Zero 2 W.
- The panel shows the image only. The headline, history and controls live in a web UI on the home network.

## Shape

A single process (`main.py serve`) runs both the daily scheduler and the Flask web UI. `main.py generate` runs the pipeline once.

```
RSS feed → positivity scores → headline → image → display
```

| Stage | Module | Notes |
|---|---|---|
| Fetch | `rss_service.py` | `requests` with timeouts and retries, parsed by `feedparser` |
| Score | `positivity.py` | Every article scored 0–1 in one structured-output call; stories at or above the threshold are selected |
| Headline | `headline.py` | Headline and subheading for the web UI |
| Image | `image_pipeline.py`, `style.py` | One image, in a fixed house style pinned by a written description and reference images; cropped and resized to 800×480 |
| Display | `display_controller.py` | Maps the image to the panel's real ink colours, refreshes, sleeps the panel |

`pipeline.py` runs the stages in order, checks for cancellation between them and writes one metadata file per run.

## Decisions

1. **One process for scheduler and web UI.** Simple to deploy as one systemd unit; a single `JobManager` makes scheduled and manual runs share one queue.
2. **Image-only panel.** No text is drawn over the picture.
3. **No good news means no new picture.** If nothing passes the positivity threshold the run is recorded as skipped and the current image is shown again, rather than lowering the bar.
4. **Ink choice is done in the app, not the driver.** The inks are far duller than pure primaries, so the app dithers against measured ink colours and sends the driver pure primaries. `omni-epd.ini` sets no dither.
5. **Style is fixed centrally.** The look comes from `style.py` and the reference images, not from per-story prompt wording.
6. **The web UI is for the home network only.** No authentication.
7. **Alerting is external.** `/health` reports run and display state for something like Home Assistant to poll.
8. **History is kept forever.** No pruning; growth is under 1 MB a day.

## Scheduling

- APScheduler runs in UTC with a `Europe/Dublin` cron trigger. A scheduler zoned to local time fired an hour late on the spring clock change.
- `misfire_grace_time=None`: the Pi has no real-time clock, so its time jumps forward after boot and jobs wake late. A late job still runs.
- Five minutes after startup a catch-up check starts a run if today's slot has passed with no success or skip since.
- A scheduled run that fails before producing an image is retried after 30 minutes and again after 2 hours. A run whose image exists but whose panel refresh failed is not retried, because that would pay for a second image; the saved image can be pushed from the history page.

## Jobs and cancellation

- `JobManager` runs one job at a time. Each job has its own cancel flag.
- Starting a job cancels the one in progress and returns immediately. The new job begins only after the old one has stopped, so two pipelines never overlap.
- A blocking API call can't be interrupted; the cancelled job stops at the next checkpoint. The OpenAI client has a 300 s timeout and one retry so this wait is bounded.

## Panel safety

- The panel is powered only for the duration of a refresh, and always ends in `sleep()` (or a power-off after a timeout).
- Refreshes are serialised by a lock and kept at least 180 s apart. Scheduled runs wait; a manual push gets a 409 instead.
- The driver's busy-wait loops forever by default, so it is given a 90 s deadline.
- SIGTERM becomes a clean shutdown: a refresh in progress is allowed to finish (up to 75 s, inside systemd's default 90 s stop timeout).

## Storage

```
data/
  images/YYYY-MM-DD-<runid>.png
  metadata/YYYY-MM-DD-<runid>.json
  current.png, current.json     symlinks to the last successful run
logs/app.log                    rotating, 2 MB × 5
```

- Only a run that both generated and displayed an image becomes "current".
- Metadata and the `current.*` links are written to a temp file and renamed into place, so a power cut can't leave a partial file.
- The filename orders runs by day only (the suffix is a random id); within a day the file's modification time gives the order.
- Run times are stored as wall-clock times in the schedule's timezone.

## Failure handling

- Any stage failure ends the run as `failed`, leaves the previous image on the panel and is visible in `/health` and the web UI.
- Malformed structured output from the text model is requested once more before the stage fails.
- There is no offline fallback image.

## Testing

`pytest` covers each stage with faked OpenAI and feed responses, the scheduler's DST and catch-up logic, job cancellation, storage ordering and atomic writes, the display controller with a fake panel, and the web endpoints. No test needs the network or the hardware.
