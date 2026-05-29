from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable, List, Optional
from zoneinfo import ZoneInfo

import feedparser

from .models import Article

LOGGER = logging.getLogger(__name__)


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


def fetch_articles(feed_url: str, *, limit: Optional[int] = None, timezone: str = "Europe/Dublin") -> List[Article]:
    LOGGER.debug("Fetching RSS feed from %s", feed_url)
    feed = feedparser.parse(feed_url)

    if feed.bozo:
        LOGGER.error("RSS parsing encountered exception", exc_info=feed.bozo_exception)
        raise RuntimeError(f"Failed to parse RSS feed: {feed.bozo_exception}")

    zone = ZoneInfo(timezone)
    entries: Iterable = feed.entries

    if limit is not None:
        entries = entries[:limit]

    articles: List[Article] = []
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
