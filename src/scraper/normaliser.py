"""URL normalisation for the NHS baby scraper.

Rules (see scraper-spec.md):
1. Resolve relative and protocol-relative hrefs against the page base URL.
2. Lowercase scheme and host.
3. Strip fragment.
4. Strip query string.
5. Trailing slash on paths without a file extension.
6. Reject non-HTTP(S) schemes.
"""

from __future__ import annotations

from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

_REJECTED_SCHEMES = frozenset({"mailto", "tel", "javascript", "ftp", "data", "file"})


def normalise(href: str, base_url: str) -> str | None:
    """Normalise a discovered href against the page base URL.

    Returns the canonical URL string, or None if it should be discarded
    (non-HTTP(S) scheme, empty, or malformed).
    """
    if not href:
        return None

    href = href.strip()
    if not href:
        return None

    # Strip fragments early — they don't survive normalisation.
    href, _ = urldefrag(href)
    if not href:
        return None

    # Reject obvious non-http(s) schemes before joining (joining mailto: with
    # a base URL doesn't strip it; better to fail fast).
    if ":" in href:
        scheme_candidate = href.split(":", 1)[0].lower()
        if scheme_candidate in _REJECTED_SCHEMES:
            return None

    absolute = urljoin(base_url, href)
    parts = urlsplit(absolute)

    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        return None

    host = parts.netloc.lower()
    if not host:
        return None

    path = parts.path or "/"
    path = _normalise_path(path)

    return urlunsplit((scheme, host, path, "", ""))


def _normalise_path(path: str) -> str:
    """Ensure the trailing slash rule: add one unless the last segment has an extension."""
    if not path:
        return "/"
    if path.endswith("/"):
        return path
    last = path.rsplit("/", 1)[-1]
    if "." in last:
        return path
    return path + "/"
