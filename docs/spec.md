# Chuckle Specification

Chuckle is a locally-run personal AI assistant that answers natural language questions about a baby's activity data exported from the Huckleberry child monitoring app. The MVP is scoped to information retrieval over Huckleberry CSV exports; a CSV is ingested into a local SQLite database, and a LangChain tool-calling agent translates user questions into SQL, executes them, and streams a natural-language answer back through a Streamlit chat UI. NHS guidance integration, multi-user support, and hosted deployment are explicitly deferred.

## 1. Overview

### 1.1 Problem statement
The Huckleberry app records detailed events about a baby's feeds, sleep, nappies, baths, medication, growth, and other activities, but its in-app analytics are fixed. Parents who want to ask ad-hoc questions ("how long did she sleep last night?", "average feed duration this week?", "when was the last poo?") have to scroll through logs or export a CSV and analyse it by hand. Chuckle removes that friction by turning the CSV into a queryable structured store and exposing it through a chat interface backed by an LLM.

### 1.2 Goals and success criteria
- A user can upload a Huckleberry CSV export and have it ingested into a local SQLite database with no manual cleanup.
- A user can ask natural-language questions in a chat UI and receive accurate, grounded answers derived from the data.
- Relative temporal expressions ("yesterday", "this week", "this morning") resolve correctly against the current datetime at query time.
- Intermediate agent state (SQL, tool errors, retries) is never surfaced; only a high-level status indicator and the final streamed answer are visible.
- The system runs end-to-end on a single local machine with no external dependencies beyond the OpenRouter API.

### 1.3 Scope and boundaries
In scope: CSV ingestion, SQLite persistence, single-table event schema, LangChain ReAct agent with a SQL tool, Streamlit chat UI with streaming and status indicators.

Out of scope: see Section 9.

### 1.4 Stakeholders
- Primary user: the parent operating the local instance.
- Implementer: the engineer building the MVP from this spec.

## 2. Requirements

### 2.1 Functional requirements

- **FR-1 CSV upload.** The UI MUST provide a file uploader that accepts a Huckleberry CSV export. On submission the file is parsed and the database is fully replaced with the contents of the upload (Huckleberry exports are always full exports).
- **FR-2 Ingestion normalisation.** Each CSV row MUST be mapped to a normalised `events` record with parsed `start_time`, `end_time`, `duration_minutes`, and a JSON `attributes` blob holding type-specific fields (see Section 4).
- **FR-3 Row-count confirmation.** After ingestion the UI MUST display the number of rows successfully ingested, broken down by `type`.
- **FR-4 Chat input.** The UI MUST provide a chat input that accepts a free-form natural-language question.
- **FR-5 Agent query loop.** A LangChain ReAct agent MUST receive the user question, optionally call the `query_database` tool one or more times, and produce a final natural-language answer. Maximum 3 tool-call attempts per user question.
- **FR-6 SQL tool.** The `query_database` tool MUST accept a SQL string, reject anything that is not a single `SELECT` statement, execute it read-only against SQLite, and return results as a list of dicts.
- **FR-7 Temporal resolution.** A `build_temporal_context(now: datetime) -> str` function MUST inject the current date, time, and day of week plus a `TIME_PERIODS` constants block into the system prompt. The LLM resolves temporal expressions and writes concrete ISO 8601 datetime strings directly into generated SQL. The LLM MUST state the time range it used at the start of any answer involving a time period, so the user can verify the interpretation.
- **FR-8 Streaming output.** The final answer MUST stream token-by-token into the chat transcript via Streamlit's streaming primitives.
- **FR-9 Status indicator.** While the agent is calling tools or thinking, the UI MUST display a high-level human-readable status (e.g. "Searching your data...") and MUST NOT display raw SQL, tool errors, or retry attempts.
- **FR-10 Conversation persistence within session.** The chat transcript MUST persist for the duration of the Streamlit session so follow-up questions are visible.

### 2.2 Non-functional requirements

- **NFR-1 Locality.** All persistence, code execution, and UI MUST run on the local machine. The only external network call is to the OpenRouter API.
- **NFR-2 Latency.** A typical question (one or two tool calls over a few thousand rows) SHOULD return a first streamed token within 3 seconds of submission on a modern laptop with a working network connection.
- **NFR-3 Determinism of data.** The same CSV uploaded twice MUST produce identical database contents.
- **NFR-4 Read-only DB access from the agent.** The agent MUST NOT be able to mutate the database; the SQL tool enforces SELECT-only.
- **NFR-5 Secrets handling.** The OpenRouter API key MUST be read from an environment variable (e.g. `OPENROUTER_API_KEY`) and MUST NOT be hard-coded or written to disk.

### 2.3 Constraints
- Single-user, single-machine.
- The Huckleberry CSV schema (columns: `Type`, `Start`, `End`, `Duration`, `Start Condition`, `Start Location`, `End Condition`, `Notes`) is fixed and cannot be influenced.
- Column semantics are overloaded per `Type`; ingestion logic must dispatch by `Type` to extract meaningful typed attributes.
- SQLite is the only datastore; no separate vector store or external service in the MVP.

## 3. Architecture

### 3.1 System overview
Three components run inside a single Streamlit process:

1. **Ingestion pipeline** (`ingest.py`) — parses the uploaded CSV, normalises rows, and writes them to SQLite.
2. **Database layer** (`db.py`) — owns the SQLite connection, schema bootstrap, and the read-only query helper used by the agent's tool.
3. **Agent** (`agent.py`) — a LangChain ReAct agent configured with one tool (`query_database`), the system prompt from `prompts.py`, and an OpenRouter-backed chat model.

The Streamlit `app.py` wires these together: it owns the upload widget, the chat widgets, the session state, and the streaming output loop.

### 3.2 Technology stack

| Layer | Choice | Rationale |
|---|---|---|
| Backend language | Python 3.11+ | Native fit for LangChain, pandas, sqlite3 stdlib. |
| Web framework | FastAPI (optional) / Streamlit | Streamlit alone is sufficient for the MVP; FastAPI is listed in the brief for a future split between UI and API but is not required for v1. |
| UI | Streamlit | Built-in `chat_message`, `write_stream`, `file_uploader`, `status` / `spinner` primitives remove the need for a custom frontend. |
| Database | SQLite (file-based) | Zero-config local persistence, sufficient for ~10^4 row scale. |
| Agent framework | LangChain (ReAct, tool-calling) | Provides the ReAct loop, retry semantics, and streaming callbacks out of the box. |
| LLM access | OpenRouter via LangChain's `ChatOpenAI` with `base_url=https://openrouter.ai/api/v1` | OpenAI-compatible surface lets us swap models without rewriting client code. |

### 3.3 Data flow

Upload path:
1. User selects CSV in the Streamlit uploader.
2. `ingest.parse_csv(file)` returns an iterable of normalised event dicts.
3. `db.replace_events(events)` truncates and re-inserts in a single transaction.
4. UI re-renders with a per-type row count.

Query path:
1. User submits a question in the chat input.
2. `app.py` appends the message to session state and invokes `agent.answer(question, now=datetime.now())`.
3. The agent issues zero or more `query_database` tool calls. Each call: validate SELECT-only -> execute against SQLite -> return rows as a list of dicts.
4. The agent's final message is streamed back to the UI via a Streamlit-compatible streaming callback handler.
5. The full transcript (user + assistant) is appended to session state.

### 3.4 Integration points
- **OpenRouter API**: outbound HTTPS only, authenticated by `OPENROUTER_API_KEY`. Model name configured by env var (e.g. `CHUCKLE_MODEL`) with a sensible default.
- **Huckleberry CSV**: inbound, manual file upload only. No live API integration.

## 4. Data models

### 4.1 `events` table

All type-specific columns are nullable. A column is only populated when `type` matches the relevant event — e.g. `feed_left_minutes` is always NULL for non-Feed rows. The CHECK constraints on enum columns serve double duty: data validation and self-documenting valid values for the LLM.

```sql
CREATE TABLE events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    type             TEXT NOT NULL CHECK(type IN (
                         'Feed', 'Sleep', 'Diaper', 'Bath',
                         'Tummy time', 'Story time', 'Pump',
                         'Meds', 'Growth', 'Temp'
                     )),
    start_time       DATETIME NOT NULL,  -- ISO 8601 string
    end_time         DATETIME,
    duration_minutes INTEGER,            -- NULL for instantaneous events

    notes            TEXT,

    -- Feed (feed_mode determines which sub-columns are populated)
    feed_mode            TEXT CHECK(feed_mode IN ('breast', 'bottle', 'solids')),
    feed_left_minutes    INTEGER,        -- breast only; nullable
    feed_right_minutes   INTEGER,        -- breast only; nullable
    feed_bottle_volume   REAL,           -- bottle only
    feed_bottle_units    TEXT CHECK(feed_bottle_units IN ('ml', 'oz')),
    feed_bottle_type     TEXT CHECK(feed_bottle_type IN (
                             'Breast Milk', 'Formula', 'Tube Feeding',
                             'Cow Milk', 'Goat Milk', 'Soy Milk', 'Other'
                         )),
    feed_solids_food     TEXT,
    feed_solids_reaction TEXT CHECK(feed_solids_reaction IN ('LOVED', 'MEH', 'HATED', 'ALLERGIC')),

    -- Diaper
    diaper_kind        TEXT CHECK(diaper_kind IN ('pee', 'poo', 'both', 'dry')),
    diaper_colour      TEXT CHECK(diaper_colour IN ('yellow', 'brown', 'black', 'green', 'red', 'gray')),
    diaper_consistency TEXT CHECK(diaper_consistency IN ('solid', 'loose', 'runny', 'mucousy', 'hard', 'pebbles', 'diarrhea')),
    diaper_amount      TEXT CHECK(diaper_amount IN ('small', 'medium', 'large')),

    -- Pump (volume = sum of both sides; CSV does not reliably distinguish left/right)
    pump_volume_ml     INTEGER,

    -- Growth
    growth_weight       REAL,
    growth_weight_units TEXT CHECK(growth_weight_units IN ('kg', 'lbs.oz')),

    -- Meds
    meds_medicine  TEXT,
    meds_dose      REAL,
    meds_dose_units TEXT CHECK(meds_dose_units IN ('ml', 'oz', 'tsp', 'drops')),

    -- Temp
    temp_value REAL,
    temp_units TEXT CHECK(temp_units IN ('C', 'F'))
);

CREATE INDEX idx_events_type  ON events(type);
CREATE INDEX idx_events_start ON events(start_time);
```

### 4.2 CSV column mapping per type

The raw CSV columns (`Duration`, `Start Condition`, `Start Location`, `End Condition`, `Notes`) are semantically overloaded — the parser dispatches by `Type` to extract the correct fields.

| Type | `duration_minutes` | Parsed columns |
|---|---|---|
| `Feed` | sum of left + right minutes | `feed_mode` ← `Start Location`; `feed_left_minutes` / `feed_right_minutes` ← `00:MMR` / `00:MML` pattern in `Start Condition` + `End Condition` |
| `Sleep` | from `Duration` (`H:MM`) | — |
| `Diaper` | NULL | `diaper_colour` ← `Duration`; `diaper_consistency` ← `Start Condition`; `diaper_kind` + `diaper_amount` ← `End Condition` (e.g. `Poo:small`) |
| `Pump` | from `Duration` | `pump_volume_ml` ← sum of `Start Condition` + `End Condition` volumes (e.g. `30ml`, `0ml`) |
| `Meds` | NULL | `meds_medicine` ← `Start Location`; `meds_dose` ← `Notes` (bare number) |
| `Growth` | NULL | `growth_weight` + `growth_weight_units` ← `Start Condition` (e.g. `6.6kg`) |
| `Temp` | NULL | `temp_value` + `temp_units` ← `Start Condition` (e.g. `37.4°C`) |
| `Bath`, `Tummy time`, `Story time` | NULL | `notes` only |

### 4.3 Parsing rules
- `Start`, `End`: parsed with a fixed format `YYYY-MM-DD H:MM`; stored as ISO 8601 strings.
- `Duration` `H:MM` → `duration_minutes = H*60 + MM` (Sleep, Pump). For Diaper, `Duration` is non-numeric (stool colour) — do not coerce.
- Feed side encoding: `00:05R` → right 5 min, `00:03L` → left 3 min. `duration_minutes` = sum.
- `pump_volume_ml`: sum both side volumes, stripping `ml`/`oz` suffix. Convert oz → ml if units are oz.
- `growth_weight`: strip unit suffix, cast to REAL, store units separately.
- `temp_value`: strip `°C`/`°F`, cast to REAL, store units separately.
- `meds_dose_units`: not reliably present in the CSV — set to NULL if absent.
- Unknown or unparseable fields: log a warning and set the column to NULL; never raise.

### 4.4 Validation rules
- `type` not in the CHECK list: log and skip the row.
- `start_time` missing: log and skip the row.
- `end_time < start_time`: log the anomaly, insert the row anyway (source of truth).
- CHECK constraint violation on an enum column (e.g. unexpected colour value): log and set the column to NULL rather than failing the insert.

### 4.5 Schema context function

`db.get_schema_context(conn)` assembles the string injected into the agent's system prompt:

1. Live DDL from `sqlite_master` — always in sync; CHECK constraints document valid enum values.
2. Static column description dict from `prompts.py` — plain-English explanations for non-obvious columns (e.g. `feed_left_minutes`: "minutes spent feeding on the left breast during this session").
3. Three sample rows per event type from the live DB.

### 4.6 Data lifecycle
- Each upload performs a transactional `DELETE FROM events; INSERT ...` — Huckleberry exports are full snapshots, no merge logic needed.
- The DB file lives at `./chuckle.db` and is gitignored.
- Parse errors and skipped rows are collected during ingestion and surfaced to the user as a warning summary after upload.

## 5. API design

There is no public HTTP API in the MVP. Internal module boundaries:

### 5.1 `ingest.py`
```python
def parse_csv(file: BinaryIO) -> list[EventRecord]: ...
```
Returns normalised event dicts. Does not touch the database.

### 5.2 `db.py`
```python
def init_db(path: str = "chuckle.db") -> sqlite3.Connection: ...
def replace_events(conn, events: Iterable[EventRecord]) -> int: ...
def run_select(conn, sql: str) -> list[dict]: ...   # raises on non-SELECT
```
`run_select` enforces SELECT-only by parsing the leading keyword after stripping comments and whitespace; anything else raises `ValueError("only SELECT statements are allowed")`.

### 5.3 `agent.py`
```python
def build_agent(now: datetime) -> AgentExecutor: ...
def answer(question: str, now: datetime) -> Iterator[str]: ...
```
`answer` yields tokens of the final assistant message. Tool calls do not yield to the caller; they update a side-channel status that the UI reads via a callback handler.

### 5.4 Tool contract
```
Tool name:        query_database
Description:      Run a single read-only SQL SELECT against the events table
                  and return the rows. Use this to answer any question about
                  the baby's activity data.
Input schema:     { "sql": "<a single SELECT statement>" }
Output:           JSON array of row objects, or an error string the agent can
                  inspect to retry. Errors returned to the agent never reach
                  the UI.
```

## 6. Prompt design

### 6.1 `build_temporal_context(now: datetime) -> str`

Pure Python function in `prompts.py` — only `strftime` calls, no date logic. Returns a string block injected into the system prompt at agent build time.

```python
TIME_PERIODS = {
    "early_morning": {"start": "05:00", "end": "08:00"},
    "morning":       {"start": "05:00", "end": "12:00"},
    "midday":        {"start": "11:00", "end": "14:00"},
    "afternoon":     {"start": "12:00", "end": "17:00"},
    "evening":       {"start": "17:00", "end": "21:00"},
    "night":         {"start": "21:00", "end": "05:00"},   # crosses midnight
    "overnight":     {"start": "22:00", "end": "07:00"},   # night feed window
}
```

Injected block (example at 2026-05-12 14:32, Tuesday):

```
Current date and time: 2026-05-12 14:32 (Tuesday)

Time-of-day periods (apply to any date):
  early_morning : 05:00 – 08:00
  morning       : 05:00 – 12:00
  midday        : 11:00 – 14:00
  afternoon     : 12:00 – 17:00
  evening       : 17:00 – 21:00
  night         : 21:00 – 05:00 (crosses midnight)
  overnight     : 22:00 – 07:00 (crosses midnight)

All SQL datetimes must use format: YYYY-MM-DD HH:MM:SS
Calculate relative dates (yesterday, last Tuesday, this week, etc.)
from the current date above.
```

### 6.2 System prompt assembly order

1. **Role**: "You are Chuckle, a helpful assistant that answers questions about a baby's activity data."
2. **Schema**: live DDL from `sqlite_master` (includes CHECK constraints with valid enum values).
3. **Column descriptions**: plain-English dict for non-obvious columns.
4. **Three sample rows per event type** from the live DB.
5. **Temporal context block**: output of `build_temporal_context(now)`.
6. **SQL conventions**: use `datetime(start_time)` for comparisons; always filter by `type`; for breast feeds, total feed time = `feed_left_minutes + feed_right_minutes`; periods that cross midnight require two conditions (`>= '21:00:00' OR < '05:00:00'`).
7. **Few-shot Q→SQL examples**:
   - "How long did she sleep last night?" → `SELECT SUM(duration_minutes) FROM events WHERE type='Sleep' AND (start_time >= '2026-05-11 21:00:00' AND start_time < '2026-05-12 05:00:00')`
   - "When was her last nappy change?" → `SELECT start_time, diaper_kind FROM events WHERE type='Diaper' ORDER BY start_time DESC LIMIT 1`
   - "Average feed duration this week?" → `SELECT AVG(feed_left_minutes + feed_right_minutes) FROM events WHERE type='Feed' AND start_time >= '2026-05-06 00:00:00' AND start_time <= '2026-05-12 23:59:59'`
   - "How much did she pump yesterday?" → `SELECT SUM(pump_volume_ml) FROM events WHERE type='Pump' AND start_time >= '2026-05-11 00:00:00' AND start_time < '2026-05-12 00:00:00'`
8. **Output rule**: "Always begin your answer by stating the time range you used (e.g. 'Looking at last night, 21:00–05:00…'). Respond in plain natural language. Do not show SQL or mention the database."

## 7. UI

### 7.1 Layout
- Sidebar: file uploader, last-ingestion summary (row count per type), a "Replace data" button gated on a file being selected.
- Main pane: chat transcript using `st.chat_message`. New input via `st.chat_input` at the bottom.

### 7.2 Streaming and status
- While the agent is running, render a `st.status("Searching your data...", state="running")` block (or `st.spinner`) above the assistant message placeholder.
- Tokens of the final assistant message are written via `st.write_stream` against a generator returned by `agent.answer`.
- The status block is collapsed/cleared once the first token of the final answer is emitted.
- Tool errors and retries are swallowed by the agent layer; only the final answer (or, if all 3 attempts fail, a fixed user-facing message: "Sorry, I couldn't answer that from the data.") reaches the UI.

### 7.3 Session state
- `st.session_state.messages: list[{"role": "user"|"assistant", "content": str}]` holds the visible transcript.
- `st.session_state.db_ready: bool` gates the chat input on a successful ingestion.

## 8. Testing strategy

### 8.1 Unit tests
- `ingest.parse_csv` against a hand-built fixture CSV containing at least one row per `type`, including malformed and edge cases (missing `End`, non-numeric `Duration` for Diaper, multi-side feed encoding). Assert exact `EventRecord` output.
- Duration / volume / weight / temperature parsers tested in isolation.
- `db.run_select` rejects `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ATTACH`, `PRAGMA`, multi-statement strings, and SELECT statements with trailing semicolons followed by another statement.

### 8.2 Integration tests
- End-to-end ingestion: load the real `huckleberry_data.csv` fixture, assert total row count and per-type counts match the documented distribution (Feed 1295, Diaper 1715, Sleep 764, Pump 205, Bath 76, Tummy time 18, Meds 15, Growth 3, Story time 6, Temp 1).
- Agent harness with a stubbed LLM that emits canned tool calls; assert that the `query_database` tool is called, SELECT-only enforcement holds, and a final answer string is produced.

### 8.3 End-to-end / evaluation
- The `evaluation_data.json` file will hold ground-truth Q&A pairs. An evaluation script (out of MVP scope to build, but the file format is reserved) runs each question through `agent.answer`, compares the answer to the expected answer using both exact match on numeric extraction and an LLM-judge for prose, and reports accuracy.
- Critical journeys to smoke-test manually before release:
  1. Fresh upload -> chat enabled -> ask "how many feeds today?" -> get a number.
  2. Re-upload same CSV -> identical row counts, no duplication.
  3. Ask a question requiring relative-time resolution ("last night's sleep total") at different times of day.
  4. Ask a question with no matching data ("when was her last temperature reading?" when no Temp rows exist) -> agent responds gracefully.

## 9. Out of scope (MVP)

- NHS data integration (future: RAG over NHS text, router agent to decide which tool to invoke).
- Authentication and multi-user support with per-user data isolation.
- Automatic Huckleberry sync (replacing manual upload).
- Deployed hosting (Fly.io / Render, only relevant once multi-user lands).
- Automated evaluation pipeline; the `evaluation_data.json` file is a reserved placeholder, ground-truth pairs to be authored manually.

## 10. File structure

```
chuckle/
├── app.py                  # Streamlit entry point
├── ingest.py               # CSV parsing and SQLite upsert
├── agent.py                # LangChain agent setup and query function
├── db.py                   # SQLite connection and schema initialisation
├── prompts.py              # System prompt and few-shot examples
├── huckleberry_data.csv    # Raw data (not committed long-term)
├── evaluation_data.json    # Ground-truth Q&A pairs (future)
├── chuckle.db              # SQLite database (gitignored)
└── docs/
    ├── spec.md             # This document
    └── implementation_thoughts.md
```

## 11. Appendix

### 11.1 Glossary
- **Huckleberry**: third-party iOS/Android app for logging baby activity; source of the CSV.
- **ReAct agent**: LangChain agent pattern that interleaves reasoning steps with tool calls.
- **OpenRouter**: LLM gateway exposing an OpenAI-compatible API across many model providers.
- **Attributes blob**: the per-row JSON column carrying type-specific fields.

### 11.2 Decision log
- **SQLite over Postgres/DuckDB**: smallest possible operational surface for a single-user local app; sufficient for ~10^4 rows; `json_extract` is built in.
- **Single `events` table with JSON attributes** over per-type tables: keeps the agent's schema reasoning trivial (one table) at the cost of slightly less SQL ergonomics; chosen because the agent generates SQL, not a human.
- **Streamlit over a custom React frontend**: chat + uploader + streaming + status indicators are first-class, removing weeks of UI work for an MVP that only needs to be usable by one person.
- **Full-replace ingestion** over upsert: Huckleberry exports are full snapshots; deduping by content hash would add complexity for no gain.
- **OpenRouter via OpenAI-compatible client**: model is TBD; the OpenAI surface lets us defer the model choice without code churn.
- **Hide all intermediate agent state from the UI**: the user is a parent, not an engineer; raw SQL and retry errors are noise, not value.
- **Max 3 tool-call attempts**: bounds latency and token cost on pathological questions while leaving room for one self-correction after a SQL error.
