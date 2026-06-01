# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Chuckle** answers natural-language questions about a baby's activity data exported from
the [Huckleberry](https://huckleberry.app/) app, supplemented by NHS [baby care](https://www.nhs.uk/baby/)
guidance. A user uploads a Huckleberry CSV in a Streamlit chat UI and asks questions; a
LangChain tool-calling agent (via OpenRouter) answers them by writing read-only SQL against
a local SQLite database.

## Commands

| Task | Command |
|---|---|
| Install deps | `uv sync` |
| Run the app | `uv run streamlit run app.py` (chat UI at http://localhost:8501) |
| Run all tests | `uv run pytest` |
| Run one test file | `uv run pytest tests/test_ingest.py` |
| Run one test | `uv run pytest tests/test_ingest.py::test_name` |
| Lint + format | `uv run ruff check src tests --fix && uv run ruff format src tests` |
| Run the NHS scraper | `uv run python scripts/scrape_nhs.py [--force \| --retry-failed] [--db PATH]` |

Scope `ruff` to `src` and `tests` only — never lint `notebooks/`.

`tests` and `pythonpath = ["src"]` are configured in `pyproject.toml`, so test modules import
`agent`, `db`, `ingest`, `prompts` directly (no `src.` prefix); scraper modules import as `src.scraper.*`.

## Configuration

Loaded from `.env` (copy `.env.example`):

- `OPENROUTER_API_KEY` (**required**) — all LLM calls go through [OpenRouter](https://openrouter.ai/api/v1).
- `CHUCKLE_MODEL` (optional, default `openai/gpt-4o-mini`) — any OpenRouter model slug. Called at `temperature=0`, streaming on.

## Architecture

Two independent subsystems share the repo but not their databases.

### 1. Query agent (`app.py` + `src/`)

The runtime pipeline for one question:

1. **Ingest** (`src/ingest.py`) — `parse_csv` turns a Huckleberry CSV into normalised event
   dicts. The flat CSV (`Type`, `Start`, `Start Condition`, etc.) is dispatched per event type
   into a wide typed schema: feed sides parsed from strings like `00:05R`, oz→ml conversion,
   diaper details split from the `End Condition`/`Notes` columns, etc. Unparseable rows are
   skipped and recorded as `warnings`, not errors.
2. **Store** (`src/db.py`) — `replace_events` writes rows into the single `events` table in
   `chuckle.db`, **replacing** any previous upload (DELETE-then-insert). The schema is one wide
   table with `CHECK` constraints encoding all allowed enum values; columns are namespaced by
   event type (`feed_*`, `diaper_*`, `pump_*`, `growth_*`, `meds_*`, `temp_*`).
3. **Prompt assembly** (`src/prompts.py`) — `build_system_prompt` composes the system prompt
   per request: role block, live DDL + column descriptions + sample rows
   (`db.get_schema_context`), **temporal context** (current datetime plus named time-of-day
   periods like `morning`/`night`/`overnight`, some flagged as crossing midnight), SQL
   conventions, date-substituted few-shot examples, and the output rule.
4. **Tool calling** (`src/agent.py`) — a `create_tool_calling_agent` with a single tool,
   `query_database`, that runs `db.run_select`. The LLM writes the SQL; the agent inspects
   results and may iterate (`max_iterations=10`).
5. **Answer** — plain natural language, instructed to begin by stating the time range used and
   to never mention SQL or the database.

**Streaming + threading** (`src/agent.py::answer`): the agent runs in a daemon thread feeding a
`queue.Queue`; `app.py` consumes it via `st.write_stream`. `_FinalAnswerHandler` buffers LLM
tokens and flushes only when a generation has **no** `tool_calls`, so tool-call JSON never leaks
into the chat. On error or iteration-limit, a single `_FALLBACK_MSG` is yielded. The OTEL span
context is captured and re-attached inside the thread so Logfire traces stay connected.

**One shared connection** is opened in `app.py` (`@st.cache_resource`) with
`check_same_thread=False` and reused by the main thread (writes) and the agent thread (reads);
the non-overlap of reads/writes is assumed, not enforced. `db_ready` is rehydrated from SQLite
on page load, so an earlier upload survives a refresh without re-uploading.

**Security**: `db.run_select` is an application-level guard — it strips SQL comments, then rejects
anything not starting with `SELECT` or containing a second statement. The connection is opened
read-write and the LLM writes raw SQL, so this is the only barrier. Known weaknesses and the
recommended hardening (read-only URI connection, row caps, removing schema/samples from the
prompt, parameterised tools that hide SQL from the LLM) are documented in `docs/security_notes.md`
— read it before changing anything touching SQL execution or the system prompt.

### 2. NHS scraper (`scripts/scrape_nhs.py` + `src/scraper/`)

A polite BFS crawler that builds a supplementary knowledge base from NHS baby-care pages into a
**separate** `nhs_scraper.db` (tables `pages` + `scraped_content`). It is run offline via the CLI,
not from the app. Pipeline modules:

- `constants.py` — seed/exception URLs, scope keywords, politeness delays, byte/redirect/page caps.
- `db.py` — `pages` work-queue (statuses: `pending`/`processed`/`failed`/`redirected`/`blocked_by_robots`)
  and `scraped_content`; `--force`/`--retry-failed` reset rows back to `pending`.
- `robots.py` / `scope.py` / `normaliser.py` — robots.txt enforcement, in-scope (host + keyword) gating, URL normalisation.
- `fetcher.py` — `httpx` GET with retries/backoff, content-type and size limits.
- `crawler.py` — `run_crawl` drives the BFS: pop pending → robots check → fetch (following redirects
  up to `MAX_REDIRECTS`) → `classify` (article vs index) → store article HTML → enqueue in-scope links.
- `classifier.py` — heuristic article/index classification.

## Data files

- `huckleberry_data.csv` — primary input (gitignored real data; `tests/fixtures/feeds_only.csv` is the test sample).
- `evaluation_data.json` — reserved ground-truth Q/A pairs for an eval runner (not yet built); currently `[]`.
- `chuckle.db` / `nhs_scraper.db` — local SQLite, gitignored, regenerated from input.

## Testing notes

Tests mirror the source layout (`tests/` for the agent, `tests/scraper/` for the crawler).
Scraper tests use `pytest-httpx` to stub HTTP and HTML fixtures under `tests/scraper/fixtures/`.
`tests/test_db_security.py` pins the SQL guard's behaviour — keep it green when touching `run_select`.
