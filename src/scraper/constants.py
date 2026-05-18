"""Project-wide constants for the NHS baby scraper."""

from __future__ import annotations

USER_AGENT = "chuckle-scraper/0.1"

SEED_URL = "https://www.nhs.uk/baby/"

EXCEPTION_URLS: frozenset[str] = frozenset(
    {
        "https://www.nhs.uk/pregnancy/labour-and-birth/giving-birth-to-twins-or-more/",
        "https://www.nhs.uk/pregnancy/labour-and-birth/early-days/",
        "https://www.nhs.uk/pregnancy/labour-and-birth/getting-to-know-your-newborn/",
    }
)

SCOPE_KEYWORDS: frozenset[str] = frozenset(
    {
        "baby",
        "babies",
        "infant",
        "infants",
        "newborn",
        "newborns",
        "toddler",
        "toddlers",
        "child",
        "children",
        "parent",
        "parents",
    }
)

ALLOWED_HOST = "www.nhs.uk"
ROBOTS_URL = "https://www.nhs.uk/robots.txt"

MAX_PAGES = 1000
MAX_HTML_BYTES = 2_000_000
MAX_REDIRECTS = 5

REQUEST_TIMEOUT_SECONDS = 30.0
RETRY_BACKOFF_SECONDS: tuple[float, float, float] = (2.0, 4.0, 8.0)
MAX_ATTEMPTS = 3

POLITENESS_DELAY_MIN_SECONDS = 1.0
POLITENESS_DELAY_MAX_SECONDS = 2.0

ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset({"text/html", "application/xhtml+xml"})

DEFAULT_DB_PATH = "nhs_scraper.db"

PROGRESS_INTERVAL = 25
