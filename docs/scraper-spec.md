# NHS Baby Scraper — Specification (v2)

## What This Is

A web crawler and scraper that collects baby-related article pages from `www.nhs.uk` and stores their raw HTML in a dedicated SQLite database. The stored content will be used as a knowledge source for a RAG (Retrieval-Augmented Generation) pipeline.

---

## Purpose

The Chuckle assistant needs a corpus of NHS baby care guidance to answer general childcare questions. This scraper builds that corpus by systematically crawling the NHS site, classifying pages as articles or indexes, and persisting the raw HTML of article pages for downstream processing (markdown conversion, chunking, embedding).

---

## Scope: Valid URLs

A URL is **in-scope** if **any** of the following are true:

1. Path starts with `/baby/`
2. URL matches one of three explicit exception prefixes:
   - `https://www.nhs.uk/pregnancy/labour-and-birth/giving-birth-to-twins-or-more/`
   - `https://www.nhs.uk/pregnancy/labour-and-birth/early-days/`
   - `https://www.nhs.uk/pregnancy/labour-and-birth/getting-to-know-your-newborn/`
3. The lowercased path (split on `/` and `-` as word boundaries) contains any of these keywords:
   - `baby`, `babies`, `infant`, `infants`, `newborn`, `newborns`, `toddler`, `toddlers`, `child`, `children`, `parent`, `parents`

The host must be `www.nhs.uk`. Other NHS subdomains are out-of-scope.

**Exception URLs are leaf nodes:** they are fetched and stored, but links found on them are NOT extracted or queued. This avoids accidental crawling of unrelated pregnancy content.

Out-of-scope URLs discovered during link extraction are silently dropped (not stored in the database).

---

## URL Normalisation

All discovered URLs are normalised before being inserted into the database. Normalisation steps:

1. Resolve relative and protocol-relative hrefs against the current page's base URL.
2. Lowercase the scheme and host.
3. Strip the fragment (`#section`).
4. Strip the entire query string (NHS uses path-based routing; no known content depends on query parameters).
5. Normalise trailing slash: paths without a file extension always get a trailing slash (e.g. `/baby` → `/baby/`).
6. Reject non-HTTP(S) schemes (`mailto:`, `tel:`, `javascript:`).

The normalised form is the primary key for `pages.url`. URLs that normalise to the same value are deduplicated automatically.

---

## Redirects

When fetching a URL produces a 3xx response:

- Follow the redirect chain (up to a reasonable limit, e.g. 5 hops).
- If the final URL is **in-scope**: store the response body under the **final URL** in `scraped_content`. Insert the final URL into `pages` as `processed` if not already present. Mark the original URL with `status = 'redirected'` and record the final URL in `redirect_target_url`.
- If the final URL is **out-of-scope**: do not store the body. Mark the original URL with `status = 'redirected'` and record the target URL.
- This ensures the canonical URL owns the content and creates an audit trail.

---

## Robots.txt

The scraper honours `https://www.nhs.uk/robots.txt` using `urllib.robotparser` (strict RFC 9309, case-sensitive path matching).

**Deliberate decision on `Disallow: /Conditions/`:** The capital-C entry is a legacy artifact from NHS's old .aspx URL scheme. Modern NHS URLs are lowercase (`/conditions/...`). Under strict RFC matching, the rule does not apply to lowercase paths. This means in-scope URLs like `/conditions/jaundice-in-babies/` are crawlable. If NHS later updates this rule to lowercase, the strict matcher will then block those URLs automatically.

URLs blocked by robots.txt are inserted into `pages` with `status = 'blocked_by_robots'` and logged at WARNING level. They are never fetched.

---

## Safety Caps

- `MAX_PAGES = 1000` — hard stop after this many pages are processed. State is preserved in the database; the crawl can be resumed by re-running.
- `MAX_HTML_BYTES = 2_000_000` — responses exceeding 2 MB are rejected. The URL is marked `status = 'failed'` with `failure_reason = 'oversized'`.
- `MAX_REDIRECTS = 5` — redirect chains longer than this fail with `failure_reason = 'redirect_loop'`.

All caps are constants in the codebase; no CLI flag required.

---

## Database Schema

A dedicated SQLite file (`nhs_scraper.db`), separate from the agent database. `PRAGMA foreign_keys = ON;` is enabled on every connection.

### `pages` — crawl queue and link registry

```sql
CREATE TABLE pages (
    url TEXT PRIMARY KEY,
    discovered_from_url TEXT,
    time_first_seen TEXT NOT NULL,
    classification TEXT CHECK (classification IN ('article', 'index')),
    status TEXT NOT NULL CHECK (status IN (
        'pending', 'processed', 'failed', 'redirected', 'blocked_by_robots'
    )),
    redirect_target_url TEXT,
    failure_reason TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_attempt TEXT
);
```

`discovered_from_url` records only the **first** page where this URL was found (not a full back-link graph). Subsequent re-discoveries are ignored by `INSERT OR IGNORE`.

### `scraped_content` — article HTML storage

```sql
CREATE TABLE scraped_content (
    url TEXT PRIMARY KEY REFERENCES pages(url),
    time_scraped TEXT NOT NULL,
    html_content TEXT NOT NULL,
    markdown_content TEXT       -- NULL initially; populated by a separate pipeline
);
```

Writes use UPSERT (`INSERT ... ON CONFLICT(url) DO UPDATE SET ...`) so re-scrapes refresh content cleanly.

**Future pipelines:**
- **Markdown pipeline:** parses `html_content` and writes Markdown to `markdown_content`.
- **Media pipeline:** parses `html_content`, extracts image/video/PDF URLs, downloads and stores them separately. PDFs are particularly important — NHS sometimes publishes leaflets and care plans as PDFs, and that content should be extracted to text for RAG.

---

## Crawl Algorithm

```
1. Initialise: ensure schema exists.
2. Fetch and parse robots.txt.
3. Seed pages table (INSERT OR IGNORE):
     - https://www.nhs.uk/baby/                       (status=pending)
     - 3 exception URLs                                (status=pending)
4. Crawl loop:
     while pending row exists and processed_count < MAX_PAGES:
         pop next pending row (oldest first)
         if blocked by robots.txt:
             mark status=blocked_by_robots, continue
         fetch with retry/backoff:
             on transient failure (5xx/429/network): retry up to 3 times with exponential backoff
             on permanent failure (4xx other than 429): mark status=failed, failure_reason=http_NNN, continue
             on oversized response: mark status=failed, failure_reason=oversized, continue
             on wrong content-type: mark status=failed, failure_reason=wrong_content_type, continue
             on redirect: handle per Redirects section, continue
         classify (article or index) — see Classification
         if classification == 'article': UPSERT into scraped_content
         if NOT one of the 3 exception URLs:
             extract <a href> links from <main>
             for each link:
                 normalise URL
                 if in-scope: INSERT OR IGNORE into pages as pending, with discovered_from_url
                 if out-of-scope: drop silently
         mark current row status=processed
5. Print final summary.
```

Exception URLs are scraped exactly once each and never have links extracted, even if discovered through normal crawl paths.

---

## Page Classification

Classification runs only against the content inside `<main id="maincontent">`. If `<main>` is absent, fall back to `<body>`.

**Two labels only:** `article` and `index`. Classification is metadata for downstream filtering, not a gate on storage — only pages classified as `article` are stored in `scraped_content`, but the threshold is biased generously toward `article`.

**Strict index criteria** (label as `index` only if ALL of these hold):
- The `<main>` content is dominated by `<ul>`/`<li>` link lists
- Fewer than 3 `<p>` tags contain ≥20 words each
- No `<h2>` or `<h3>` is followed by substantive prose (≥1 `<p>` with ≥20 words)

**Otherwise → `article`.** This deliberately biases toward storing too much; false positives (index treated as article) are low-cost in RAG (poor embedding match → not retrieved), but false negatives (article treated as index → discarded) are hard to recover.

Thresholds must be validated against fixture HTML before merge (see Testing).

---

## Politeness

- **User-Agent**: `chuckle-scraper/0.1`
- **Delay between requests**: 1–2 seconds, uniform random jitter
- **Sequential** — no concurrent requests
- `robots.txt` honoured (see Robots.txt section)

---

## Error Handling & Retry

**HTTP response classification:**

| Response | Action |
|---|---|
| 2xx | Process normally |
| 3xx | Follow redirect (max 5 hops) |
| 429 | Transient — retry with exponential backoff |
| 5xx | Transient — retry with exponential backoff |
| 408, network timeout, connection error | Transient — retry with exponential backoff |
| 4xx (other) | Permanent — mark `failed`, do not retry |

**Retry policy:** up to 3 attempts total, exponential backoff (2s, 4s, 8s, with jitter).

**`attempt_count` semantics:** lifetime. A page that has failed three times stays `failed` across runs. To retry failed pages, use `--retry-failed`, which resets `status = 'failed'` rows back to `pending` and zeroes `attempt_count`. Permanent failures (e.g. 404) are also reset by this flag — the user is expected to know what they're doing.

**`failure_reason` values:**
`http_NNN` (e.g. `http_404`, `http_500`), `network_error`, `timeout`, `oversized`, `wrong_content_type`, `redirect_loop`, `too_many_attempts`.

---

## Re-run Behaviour

- **Default:** skip URLs with `status IN ('processed', 'failed', 'redirected', 'blocked_by_robots')`. Only `pending` rows are processed.
- **`--retry-failed`:** reset `failed` rows to `pending` (zeroing `attempt_count`).
- **`--force`:** reset ALL rows except `blocked_by_robots` to `pending` and re-fetch everything. `scraped_content` is updated in-place via UPSERT.

---

## Observability

- **Per-URL log line** to stdout: `[ISO timestamp] STATUS url classification=X duration=Yms bytes=Z`
- **Progress counter** every 25 processed pages: `progress: N processed, M failed, P pending, R redirected`
- **Final summary** on exit:
  ```
  Crawl complete in HH:MM:SS
    Processed: N (articles: A, indexes: I)
    Failed:    M (broken down by failure_reason)
    Redirected: R
    Blocked by robots: B
    Pending (unfinished): P
  ```
- **Errors and warnings** to stderr.
- **Log level** via env var `LOG_LEVEL` (default `INFO`; supports `DEBUG`).
- Robots.txt disallowances logged at `WARNING` so they aren't silently lost.

---

## Implementation

**Language:** Python (managed with `uv`).

**Key dependencies:**
- `httpx` — HTTP client
- `beautifulsoup4` — HTML parsing
- `sqlite3` — stdlib
- `urllib.robotparser` — stdlib

**Location:** `src/scraper/` — separate from agent source code. A future refactor will unify everything under `src/`.

**Entry point:**
```bash
uv run -m src.scraper.main [--force] [--retry-failed]
```

**Modules:**
- `db.py` — schema creation, all DB reads/writes, UPSERT helpers
- `normaliser.py` — URL normalisation
- `scope.py` — in-scope/out-of-scope determination, keyword rule
- `fetcher.py` — HTTP fetch with retry/backoff, Content-Type/size checks, redirect handling
- `classifier.py` — article vs index classification (operates on `<main>`)
- `crawler.py` — BFS loop, link extraction
- `main.py` — CLI entry, wires everything together, prints summary

---

## Testing

Tests are unit-level using fixture HTML — no live HTTP. Three real fixtures are saved under `tests/fixtures/`:

- `nhs_baby_index.html` — `https://www.nhs.uk/baby/` (index)
- `nhs_caring_for_newborn_index.html` — `https://www.nhs.uk/baby/caring-for-a-newborn/` (index)
- `nhs_helping_baby_sleep_article.html` — `https://www.nhs.uk/baby/caring-for-a-newborn/helping-your-baby-to-sleep/` (article)

**Test coverage:**

1. **URL normalisation** — fragment stripping, query stripping, case lowering, trailing slash, relative/protocol-relative resolution, scheme rejection.
2. **Scope rules** — `/baby/*` accepted, exceptions accepted, keyword-matching URLs accepted, other NHS pages rejected, non-NHS hosts rejected.
3. **Link extraction** — given a fixture, returns the expected set of in-scope and out-of-scope URLs.
4. **Classifier** — fixture #1 and #2 classify as `index`; fixture #3 classifies as `article`. Threshold edge cases covered.
5. **Database** — INSERT OR IGNORE deduplication, UPSERT refresh, FK enforcement, CHECK constraint rejection of bad status/classification values.
6. **Exception-URL link skipping** — links found on an exception URL are not queued.

A small integration smoke test (live HTTP) is acceptable but not required for the DoD.

---

## Verification (Definition of Done)

1. Unit tests pass (`uv run pytest`).
2. `uv run ruff check . && uv run ruff format .` clean.
3. A first live run against `www.nhs.uk` populates the database, processes the seed URLs and at least the three known article fixture URLs, and exits cleanly with a summary.
4. Re-running without flags is a near-no-op (only any newly-discovered or `pending` URLs are processed).
5. Re-running with `--force` refreshes all `scraped_content` rows without errors (UPSERT works).
6. `--retry-failed` correctly re-queues prior failures.
7. `pages.status` distribution is sensible (no rows in unexpected states; `failed` rows have a populated `failure_reason`).
8. Spot-check: all `article`-classified rows have non-empty `html_content`; no `index`-classified rows appear in `scraped_content`.
9. The three exception URLs are present in `scraped_content` with classification `article`.
