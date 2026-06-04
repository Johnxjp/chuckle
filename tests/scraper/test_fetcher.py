"""Unit tests for scraper.fetcher."""

from __future__ import annotations

import httpx
import pytest

from src.scraper import fetcher
from src.scraper.constants import MAX_HTML_BYTES, USER_AGENT


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(fetcher, "_sleep_backoff", lambda _i: None)


def _client():
    return httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=False, timeout=5.0)


def test_fetch_ok_returns_body(httpx_mock):
    httpx_mock.add_response(
        url="https://www.nhs.uk/baby/",
        status_code=200,
        headers={"Content-Type": "text/html; charset=utf-8"},
        text="<html>hello</html>",
    )
    with _client() as client:
        result = fetcher.fetch("https://www.nhs.uk/baby/", client=client)
    assert result.kind == "ok"
    assert result.status == 200
    assert "hello" in (result.body or "")
    assert result.attempts == 1


def test_fetch_4xx_fails_immediately(httpx_mock):
    httpx_mock.add_response(
        url="https://www.nhs.uk/baby/missing/",
        status_code=404,
        headers={"Content-Type": "text/html"},
        text="not found",
    )
    with _client() as client:
        result = fetcher.fetch("https://www.nhs.uk/baby/missing/", client=client)
    assert result.kind == "failed"
    assert result.failure_reason == "http_404"
    assert result.attempts == 1


def test_fetch_5xx_retries_then_fails(httpx_mock):
    for _ in range(3):
        httpx_mock.add_response(
            url="https://www.nhs.uk/baby/",
            status_code=503,
            text="boom",
        )
    with _client() as client:
        result = fetcher.fetch("https://www.nhs.uk/baby/", client=client)
    assert result.kind == "failed"
    assert result.failure_reason == "http_503"
    assert result.attempts == 3


def test_fetch_429_retries_then_recovers(httpx_mock):
    httpx_mock.add_response(url="https://www.nhs.uk/baby/", status_code=429)
    httpx_mock.add_response(url="https://www.nhs.uk/baby/", status_code=429)
    httpx_mock.add_response(
        url="https://www.nhs.uk/baby/",
        status_code=200,
        headers={"Content-Type": "text/html"},
        text="<html/>",
    )
    with _client() as client:
        result = fetcher.fetch("https://www.nhs.uk/baby/", client=client)
    assert result.kind == "ok"
    assert result.attempts == 3


def test_fetch_redirect_returns_target(httpx_mock):
    httpx_mock.add_response(
        url="https://www.nhs.uk/baby/old/",
        status_code=301,
        headers={"Location": "https://www.nhs.uk/baby/new/"},
    )
    with _client() as client:
        result = fetcher.fetch("https://www.nhs.uk/baby/old/", client=client)
    assert result.kind == "redirect"
    assert result.redirect_target == "https://www.nhs.uk/baby/new/"


def test_fetch_rejects_non_html_content_type(httpx_mock):
    httpx_mock.add_response(
        url="https://www.nhs.uk/baby/leaflet.pdf",
        status_code=200,
        headers={"Content-Type": "application/pdf"},
        content=b"%PDF-1.4",
    )
    with _client() as client:
        result = fetcher.fetch("https://www.nhs.uk/baby/leaflet.pdf", client=client)
    assert result.kind == "failed"
    assert result.failure_reason == "wrong_content_type"


def test_fetch_rejects_oversized_response(httpx_mock):
    big = b"x" * (MAX_HTML_BYTES + 1)
    httpx_mock.add_response(
        url="https://www.nhs.uk/baby/huge/",
        status_code=200,
        headers={"Content-Type": "text/html"},
        content=big,
    )
    with _client() as client:
        result = fetcher.fetch("https://www.nhs.uk/baby/huge/", client=client)
    assert result.kind == "failed"
    assert result.failure_reason == "oversized"
