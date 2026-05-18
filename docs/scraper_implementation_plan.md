
---

# NHS Baby Scraper — Tracer Bullet Plan

This section tracks the NHS baby scraper, a distinct subsystem from the main Chuckle agent. Spec: `docs/scraper-spec.md`. Code lives under `src/scraper/` with its own SQLite database (`nhs_scraper.db`). Stages follow the tracer-bullet approach: each ends with a runnable, verifiable artifact that does more than the previous one.

## SC-0 — Scaffold
**Goal:** Project structure and deps in place.
**Deps:** TB-0.

### T-SC-0.1 Module layout
Create empty modules under `src/scraper/`: `__init__.py`, `db.py`, `normaliser.py`, `scope.py`, `fetcher.py`, `classifier.py`, `crawler.py`, `main.py`. Create `tests/scraper/` with `conftest.py`.
**DoD:** `uv run python -c "import src.scraper.main"` runs without error.

### T-SC-0.2 Dependencies
Add `httpx` and `beautifulsoup4` (runtime); `pytest-httpx` (dev).
**DoD:** `uv sync` succeeds.

---

## SC-1 — Single-page fetch
**Goal:** Fetch one hardcoded URL and store its raw HTML in a fresh SQLite DB. No crawl loop, no classification, no robots.txt.
**Deps:** SC-0.

### T-SC-1.1 Schema + connection helper in `db.py`
- `connect(path)` enables `PRAGMA foreign_keys = ON;`.
- `init_schema(conn)` creates `pages` and `scraped_content` with full CHECK + FK constraints from spec.
- UPSERT helper for `scraped_content` writes.
**DoD:** unit test creates a fresh DB; CHECK rejects invalid status; FK rejects orphan content row.

### T-SC-1.2 Bare fetcher in `fetcher.py`
- `fetch(url, user_agent) -> (status, headers, body_bytes)` using `httpx.get()` with 30s timeout. No retry, no caps yet.
**DoD:** unit test using `pytest-httpx` returns mocked content.

### T-SC-1.3 Minimal `main.py`
Hardcode `https://www.nhs.uk/baby/`. Initialise DB, fetch, insert into `pages` and UPSERT into `scraped_content`.
**DoD:** `uv run -m src.scraper.main` against live site creates `nhs_scraper.db` with one row in each table; `length(html_content) > 5000`.

**SC-1 DoD:** Single row of real NHS baby HTML stored end-to-end. Manual SQL inspection confirms HTML matches `curl` output.

---

## SC-2 — BFS crawl within `/baby/`
**Goal:** Walk `/baby/` recursively with polite delay. Hard cap at `MAX_PAGES = 1000`.
**Deps:** SC-1.

### T-SC-2.1 URL normalisation in `normaliser.py`
Implement all rules in spec § URL Normalisation. Pure function.
**DoD:** parametrised unit tests for each rule (fragment, query, case, trailing slash, relative resolution, scheme rejection).

### T-SC-2.2 Scope check (interim) in `scope.py`
`is_in_scope(url) -> bool`: true only when path starts with `/baby/` AND host is `www.nhs.uk`. Keyword expansion deferred to SC-6.
**DoD:** unit tests for representative in/out cases.

### T-SC-2.3 Link extraction in `crawler.py`
`extract_links(html, base_url) -> list[str]` parses `<a href>` from `<main>` (fallback `<body>`), normalises and deduplicates.
**DoD:** unit test against a captured `/baby/` fixture returns the nine expected sub-topic links.

### T-SC-2.4 BFS loop
Pop oldest `pending` row; fetch; extract links; `INSERT OR IGNORE` in-scope links as `pending`; drop out-of-scope silently; mark current `processed`. Stop at `MAX_PAGES`. Random 1–2s delay between requests.
**DoD:** unit test with `pytest-httpx` confirms BFS order, queue inserts, and cap enforcement.

### T-SC-2.5 Seed run
Seed `https://www.nhs.uk/baby/` as `pending` on first run.
**DoD:** live run completes under MAX_PAGES; every URL in `pages` starts with `/baby/`.

**SC-2 DoD:** Live crawl of `/baby/*` finishes without errors. DB inspection shows no duplicates, no out-of-scope URLs, ~50–150 total rows.

---

## SC-3 — Classification
**Goal:** Distinguish article from index; only persist articles.
**Deps:** SC-2.

### T-SC-3.1 Fixture capture
Save raw HTML of the three benchmark pages as `tests/scraper/fixtures/{baby_index,caring_for_newborn_index,helping_baby_sleep_article}.html`.
**DoD:** fixtures committed, non-empty.

### T-SC-3.2 Classifier in `classifier.py`
`classify(html) -> Literal['article', 'index']`. Operates on `<main>` (fallback `<body>`). Strict index criteria per spec — bias toward `article`.
**DoD:** unit tests using the three fixtures pass; edge-case tests covered.

### T-SC-3.3 Wire classifier into crawl loop
After fetch, classify. UPSERT into `scraped_content` only when `article`. Record `classification` on every `pages` row.
**DoD:** live re-run — `scraped_content` row count equals `SELECT COUNT(*) FROM pages WHERE classification = 'article'`; no index URLs appear in `scraped_content`.

**SC-3 DoD:** Spot-check 5 known articles and 5 known indexes — all correctly classified. Sample three `scraped_content` rows — all real prose articles.

---

## SC-4 — Robots.txt + content-type + size cap
**Goal:** Compliance + safety against binaries and oversized responses.
**Deps:** SC-2.

### T-SC-4.1 Robots.txt loader
Fetch `https://www.nhs.uk/robots.txt` once at startup; parse with `urllib.robotparser` (strict, case-sensitive). Cache. Check before every fetch; disallowed URLs → `status = 'blocked_by_robots'`, log WARNING, do not fetch.
**DoD:** unit test with a mocked disallow rule confirms URL is recorded but never fetched.

### T-SC-4.2 Content-Type check
After fetch, before parsing: if `Content-Type` is not `text/html` or `application/xhtml+xml`, mark `failed` with `failure_reason = 'wrong_content_type'`.
**DoD:** unit test with mocked PDF response confirms rejection.

### T-SC-4.3 Size cap
Abort fetch and discard if body exceeds `MAX_HTML_BYTES = 2_000_000`. Mark `failed` with `failure_reason = 'oversized'`.
**DoD:** unit test with mocked >2 MB response confirms rejection.

**SC-4 DoD:** Live re-run still completes. `SELECT * FROM pages WHERE status='blocked_by_robots'` — only paths actually disallowed by NHS robots.txt. No oversized rows.

---

## SC-5 — Retry, redirects, failure tracking
**Goal:** Handle transient errors and redirect chains correctly. Permanent failures don't waste backoff time.
**Deps:** SC-3.

### T-SC-5.1 Retry policy in `fetcher.py`
Up to 3 attempts, exponential backoff (2s/4s/8s with jitter). Retry only on 5xx, 429, network errors, timeout. Increment `attempt_count` each try. Populate `failure_reason` on final failure (`http_NNN`, `network_error`, `timeout`).
**DoD:** unit tests with `pytest-httpx` cover each path; assert attempt counts and final state.

### T-SC-5.2 Redirect handling
Allow up to 5 hops. On 3xx: mark original `redirected`, record `redirect_target_url`. If final URL in-scope, UPSERT content under final URL (insert into `pages` if absent). If out-of-scope, drop body.
**DoD:** unit tests for in-scope and out-of-scope redirect targets.

**SC-5 DoD:** Inject a known 404 URL into `pages` and run — marked `failed`, `failure_reason='http_404'`, `attempt_count=1`. Inject a redirecting URL — marked `redirected` with correct target; final URL has content.

---

## SC-6 — Expanded scope (keywords + exception URLs)
**Goal:** Capture baby-relevant pages outside `/baby/` (e.g. `/conditions/jaundice-in-babies/`) and the three pregnancy exception articles.
**Deps:** SC-2.

### T-SC-6.1 Keyword scope rule
Extend `is_in_scope`: also true when lowercased path contains any of `baby`, `babies`, `infant`, `infants`, `newborn`, `newborns`, `toddler`, `toddlers`, `child`, `children`, `parent`, `parents` as a path-segment word (treat `/` and `-` as word boundaries).
**DoD:** unit tests for representative cases including `/conditions/jaundice-in-babies/` (in), `/conditions/heart-disease/` (out), `/children/dental-care/` (in).

### T-SC-6.2 Exception URL seeding
Seed the three exception URLs as `pending` on every run (`INSERT OR IGNORE`).
**DoD:** fresh DB run shows those rows present.

### T-SC-6.3 Skip link extraction on exception URLs
Maintain a constant set. When processing an exception URL: fetch, classify, UPSERT — but **do not** extract links.
**DoD:** unit test confirms link extraction is skipped only for exception URLs.

**SC-6 DoD:** Live re-run picks up at least one `/conditions/...-babies/` URL automatically. The three exception URLs all present in `scraped_content` regardless of inbound links.

---

## SC-7 — Re-run control flags
**Goal:** Idempotent default; explicit flags for refresh and retry.
**Deps:** SC-5.

### T-SC-7.1 `--retry-failed` flag
Reset `status='failed'` rows to `pending`, zero `attempt_count`, null `failure_reason`.
**DoD:** unit test; live test triggers retry of prior failure.

### T-SC-7.2 `--force` flag
Reset ALL rows except `blocked_by_robots` to `pending`, zero `attempt_count`. UPSERT logic already covers re-writes.
**DoD:** unit test; live test: re-runs with `--force` complete with no UNIQUE errors, `scraped_content` row content refreshed (`time_scraped` advances).

**SC-7 DoD:** Default re-run is a near no-op. `--retry-failed` only retries failures. `--force` re-fetches everything cleanly.

---

## SC-8 — Observability + final acceptance
**Goal:** A real crawl is monitorable, summarisable, and meets the spec's full DoD.
**Deps:** SC-3, SC-4, SC-5, SC-6, SC-7.

### T-SC-8.1 Per-URL log line
Emit `[ISO timestamp] STATUS url classification=X duration=Yms bytes=Z` to stdout. Errors/warnings to stderr. `LOG_LEVEL` env var controls verbosity.
**DoD:** live run produces a sensible stream.

### T-SC-8.2 Progress counter
Every 25 processed pages: `progress: N processed, M failed, P pending, R redirected`.
**DoD:** visible during live run.

### T-SC-8.3 Final summary
On exit: total processed (article/index split), failed (broken down by `failure_reason`), redirected, blocked, pending. Wall-clock duration.
**DoD:** matches `pages` table state.

### T-SC-8.4 Full live verification run
Fresh DB. Run scraper to completion (or MAX_PAGES). Verify every criterion in `scraper-spec.md § Verification`.
**DoD:** all 9 spec criteria hold.

**SC-8 DoD:** Acceptance review — every item in `scraper-spec.md § Verification` ticked off. Final summary printed at end of crawl matches DB.

---

## Dependency graph (scraper)

```
SC-0 ──► SC-1 ──► SC-2 ──┬─► SC-3 ──► SC-5 ─┐
                          ├─► SC-4 ─────────┤
                          └─► SC-6 ─────────┤
                                            ▼
                                          SC-7 ──► SC-8
```

SC-3, SC-4, SC-6 are mutually independent once SC-2 lands. SC-5 depends on SC-3 (it uses the UPSERT path for redirects to in-scope targets).

## Parallelisation guidance (scraper)

After SC-2, with two engineers:
- Engineer A: SC-3 (classifier) → SC-5 (retries + redirects).
- Engineer B: SC-4 (robots.txt + content-type + size) → SC-6 (scope expansion).

Both converge on SC-7 and SC-8.

## Risks and watch-outs (scraper)

- **Classifier thresholds are derived from three fixtures.** Reality might bring NHS pages that break the rule (e.g. a short news-style article that looks like an index). Bias toward `article` is the safety net; revisit if false-negatives appear in the live run.
- **Robots.txt strict matching** is a deliberate choice (see `scraper-spec.md § Robots.txt`). If NHS updates the file to lowercase `/conditions/`, the matcher will start blocking jaundice-relevant URLs without warning — watch the SC-8 summary's `blocked_by_robots` count between runs.
- **MAX_PAGES = 1000** is a safety cap, not a target. If a real crawl hits it, investigate before raising — the most likely cause is a crawler trap or an over-eager keyword match.
- **httpx default redirect following** is enabled. We override that (we want to *observe* redirects, not silently follow them) — make sure `follow_redirects=False` is set in SC-5.
