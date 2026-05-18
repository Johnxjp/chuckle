"""Scope rules for the NHS baby scraper.

A URL is in-scope if any of:
  - host is www.nhs.uk AND path starts with /baby/
  - URL is one of the three exception URLs
  - host is www.nhs.uk AND a lowercased path token matches a scope keyword
"""

from __future__ import annotations

from urllib.parse import urlsplit

from src.scraper.constants import ALLOWED_HOST, EXCEPTION_URLS, SCOPE_KEYWORDS


def is_exception_url(url: str) -> bool:
    """Return True iff the URL is one of the three explicit exception URLs."""
    return url in EXCEPTION_URLS


def is_in_scope(url: str) -> bool:
    """Decide whether a normalised URL should be enqueued for crawling."""
    if url in EXCEPTION_URLS:
        return True

    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        return False
    if parts.netloc.lower() != ALLOWED_HOST:
        return False

    path = parts.path or "/"
    if path.startswith("/baby/") or path == "/baby/":
        return True

    return _path_has_keyword(path)


def _path_has_keyword(path: str) -> bool:
    """Tokenise path on '/' and '-' and check against the keyword set."""
    lowered = path.lower()
    # Replace separators with spaces, then split on whitespace.
    for sep in ("/", "-"):
        lowered = lowered.replace(sep, " ")
    tokens = lowered.split()
    return any(token in SCOPE_KEYWORDS for token in tokens)
