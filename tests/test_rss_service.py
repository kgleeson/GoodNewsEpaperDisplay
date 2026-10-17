import pytest
import requests

from epaper_goodnews import rss_service

FEED = """<?xml version="1.0" encoding="{enc}"?>
<rss version="2.0"><channel><title>t</title>
<item><title>Fáilte {amp} first</title><link>https://example.ie/1</link><guid>1</guid></item>
<item><title>Second</title><link>https://example.ie/2</link><guid>2</guid></item>
</channel></rss>"""


class FakeResponse:
    def __init__(self, body: bytes, content_type="application/rss+xml"):
        self.content = body
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self):
        pass


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(rss_service.time, "sleep", lambda s: None)


def serve(monkeypatch, body: bytes):
    monkeypatch.setattr(rss_service.requests, "get", lambda *a, **k: FakeResponse(body))


def test_harmless_bozo_still_returns_entries(monkeypatch):
    # Declared us-ascii but UTF-8 bytes, plus a bare '&': feedparser sets bozo.
    serve(monkeypatch, FEED.format(enc="us-ascii", amp="&").encode("utf-8"))
    articles = rss_service.fetch_articles("https://example.ie/feed")
    assert [a.guid for a in articles] == ["1", "2"]


def test_unparseable_feed_raises(monkeypatch):
    serve(monkeypatch, b"<html>not a feed")
    with pytest.raises(RuntimeError, match="parse"):
        rss_service.fetch_articles("https://example.ie/feed")


def test_retries_transient_errors(monkeypatch):
    calls = []

    def flaky_get(*args, **kwargs):
        calls.append(kwargs.get("timeout"))
        if len(calls) < 3:
            raise requests.ConnectionError("Temporary failure in name resolution")
        return FakeResponse(FEED.format(enc="utf-8", amp="and").encode())

    monkeypatch.setattr(rss_service.requests, "get", flaky_get)
    assert len(rss_service.fetch_articles("https://example.ie/feed")) == 2
    assert calls == [rss_service.REQUEST_TIMEOUT] * 3


def test_gives_up_after_retries(monkeypatch):
    def down(*args, **kwargs):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(rss_service.requests, "get", down)
    with pytest.raises(RuntimeError, match="fetch"):
        rss_service.fetch_articles("https://example.ie/feed")
