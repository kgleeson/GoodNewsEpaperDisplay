from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from datetime import datetime
from zoneinfo import ZoneInfo

import feedparser
import requests

from .models import Article

LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = (10, 30)  # connect, read (seconds)
RETRY_DELAYS_SECONDS = (30, 120)
USER_AGENT = "epaper-goodnews/0.1"


def _parse_datetime(entry: dict, default_tz: ZoneInfo) -> datetime:
    if "published_parsed" in entry and entry.published_parsed:
        dt = datetime(*entry.published_parsed[:6])
    elif "updated_parsed" in entry and entry.updated_parsed:
        dt = datetime(*entry.updated_parsed[:6])
    else:
        dt = datetime.now(tz=default_tz)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=default_tz)
    return dt.astimezone(default_tz)


def _normalize_summary(entry: dict) -> str:
    summary = entry.get("summary") or entry.get("description") or ""
    return summary.strip()


def _extract_guid(entry: dict) -> str:
    guid = entry.get("id") or entry.get("guid") or entry.get("link")
    if not guid:
        guid = entry.get("title", "") + str(entry.get("published_parsed"))
    return guid


def _download(feed_url: str) -> requests.Response:
    """GET the feed with a timeout, retrying transient network failures."""
    last_exc: Exception | None = None
    for attempt, delay in enumerate((*RETRY_DELAYS_SECONDS, None), start=1):
        try:
            response = requests.get(
                feed_url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT}
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_exc = exc
            if delay is None:
                break
            LOGGER.warning(
                "RSS fetch attempt %d failed (%s); retrying in %d s", attempt, exc, delay
            )
            time.sleep(delay)
    raise RuntimeError(f"Failed to fetch RSS feed: {last_exc}") from last_exc


def fetch_articles(
    feed_url: str, *, limit: int | None = None, timezone: str = "Europe/Dublin"
) -> list[Article]:
    LOGGER.debug("Fetching RSS feed from %s", feed_url)
    response = _download(feed_url)
    feed = feedparser.parse(
        response.content,
        response_headers={"content-type": response.headers.get("Content-Type", "")},
    )

    if feed.bozo:
        # feedparser flags harmless issues (charset mismatch, stray '&') while still
        # parsing every entry; only fail if nothing usable came back.
        if not feed.entries:
            LOGGER.error("RSS parsing encountered exception", exc_info=feed.bozo_exception)
            raise RuntimeError(f"Failed to parse RSS feed: {feed.bozo_exception}")
        LOGGER.warning("RSS feed parsed with warning: %s", feed.bozo_exception)

    zone = ZoneInfo(timezone)
    entries: Iterable = feed.entries

    if limit is not None:
        entries = entries[:limit]

    articles: list[Article] = []
    for entry in entries:
        try:
            article = Article(
                guid=_extract_guid(entry),
                title=(entry.get("title") or "").strip(),
                summary=_normalize_summary(entry),
                link=entry.get("link", "").strip(),
                published=_parse_datetime(entry, zone),
            )
            articles.append(article)
        except Exception as exc:  # pragma: no cover - defensive logging
            LOGGER.warning("Failed to parse RSS entry", exc_info=exc)

    LOGGER.info("Fetched %d articles from feed", len(articles))
    return articles
