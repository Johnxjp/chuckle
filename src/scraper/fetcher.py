"""HTTP fetcher for the NHS baby scraper.

Single-hop fetch with retry/backoff, content-type guard, and size cap.
The crawler is responsible for following redirect chains across multiple
fetch() calls so each hop can be observed and rate-limited.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Literal

import httpx

from src.scraper.constants import (
    ALLOWED_CONTENT_TYPES,
    MAX_ATTEMPTS,
    MAX_HTML_BYTES,
    REQUEST_TIMEOUT_SECONDS,
    RETRY_BACKOFF_SECONDS,
    USER_AGENT,
)

log = logging.getLogger(__name__)

ResultKind = Literal["ok", "redirect", "failed"]


@dataclass(frozen=True)
class FetchResult:
    kind: ResultKind
    url: str
    attempts: int
    status: int | None = None
    body: str | None = None
    bytes_read: int = 0
    redirect_target: str | None = None
    failure_reason: str | None = None
    duration_ms: int = 0


def build_client() -> httpx.Client:
    """Build the shared httpx client. follow_redirects is OFF on purpose."""
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=False,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )


def _sleep_backoff(attempt_index: int) -> None:
    """Sleep for backoff seconds, with up to 25% jitter."""
    base = RETRY_BACKOFF_SECONDS[min(attempt_index, len(RETRY_BACKOFF_SECONDS) - 1)]
    jitter = random.uniform(0, base * 0.25)
    time.sleep(base + jitter)


def fetch(url: str, *, client: httpx.Client, start_attempt: int = 0) -> FetchResult:
    """Fetch a single URL with retry/backoff. Does not follow redirects.

    `start_attempt` is the prior lifetime attempt count for this URL — newly-
    failed retries continue counting from here so the DB column reflects the
    full lifetime tally.
    """
    attempts = start_attempt
    last_transient: str | None = None
    started = time.monotonic()

    while attempts < start_attempt + MAX_ATTEMPTS:
        attempts += 1
        try:
            response = client.get(url)
        except httpx.TimeoutException:
            last_transient = "timeout"
            if attempts - start_attempt < MAX_ATTEMPTS:
                _sleep_backoff(attempts - start_attempt - 1)
            continue
        except httpx.HTTPError as exc:
            log.debug("network error fetching %s: %s", url, exc)
            last_transient = "network_error"
            if attempts - start_attempt < MAX_ATTEMPTS:
                _sleep_backoff(attempts - start_attempt - 1)
            continue

        status = response.status_code

        if 300 <= status < 400:
            target = response.headers.get("Location")
            duration_ms = int((time.monotonic() - started) * 1000)
            return FetchResult(
                kind="redirect",
                url=url,
                attempts=attempts,
                status=status,
                redirect_target=target,
                duration_ms=duration_ms,
            )

        if status == 429 or 500 <= status < 600:
            last_transient = f"http_{status}"
            if attempts - start_attempt < MAX_ATTEMPTS:
                _sleep_backoff(attempts - start_attempt - 1)
            continue

        if status >= 400:
            duration_ms = int((time.monotonic() - started) * 1000)
            return FetchResult(
                kind="failed",
                url=url,
                attempts=attempts,
                status=status,
                failure_reason=f"http_{status}",
                duration_ms=duration_ms,
            )

        content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type and content_type not in ALLOWED_CONTENT_TYPES:
            duration_ms = int((time.monotonic() - started) * 1000)
            return FetchResult(
                kind="failed",
                url=url,
                attempts=attempts,
                status=status,
                failure_reason="wrong_content_type",
                duration_ms=duration_ms,
            )

        body_bytes = response.content
        if len(body_bytes) > MAX_HTML_BYTES:
            duration_ms = int((time.monotonic() - started) * 1000)
            return FetchResult(
                kind="failed",
                url=url,
                attempts=attempts,
                status=status,
                failure_reason="oversized",
                bytes_read=len(body_bytes),
                duration_ms=duration_ms,
            )

        try:
            body = response.text
        except UnicodeDecodeError:
            body = body_bytes.decode("utf-8", errors="replace")

        duration_ms = int((time.monotonic() - started) * 1000)
        return FetchResult(
            kind="ok",
            url=url,
            attempts=attempts,
            status=status,
            body=body,
            bytes_read=len(body_bytes),
            duration_ms=duration_ms,
        )

    duration_ms = int((time.monotonic() - started) * 1000)
    return FetchResult(
        kind="failed",
        url=url,
        attempts=attempts,
        failure_reason=last_transient or "network_error",
        duration_ms=duration_ms,
    )
