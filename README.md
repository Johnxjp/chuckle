# Chuckle

An assistant that answers questions about a baby's activity data exported from the
child-monitoring app [Huckleberry](https://huckleberry.app/). Queries are also
supported with information from the NHS about [baby care](https://www.nhs.uk/baby/).

## Capabilities

### Guidance and Support

1. Answer questions about the baby using data ingested from a Huckleberry CSV export.
2. Provide support on general childcare queries based on NHS information.

#### Supported Queries

For example:

- "How long did she sleep last night?"
- "When was her last nappy change?"
- "How many feeds in total this week?"
- "How much did she pump yesterday?"

More information about Huckleberry's official AI is
[here](https://huckleberry.zendesk.com/hc/en-us/articles/44561361627667-What-is-Berry).

## Getting started

1. Copy `.env.example` to `.env` and set `OPENROUTER_API_KEY` (and optionally
   `CHUCKLE_MODEL`).
2. Install dependencies:
   ```bash
   uv sync
   ```
3. Launch the app:
   ```bash
   uv run streamlit run app.py
   ```
   This opens the chat UI in your browser (usually http://localhost:8501).
4. In the sidebar, upload a Huckleberry CSV export (e.g. `huckleberry_data.csv`).
   The sidebar shows the ingested row count and a per-event-type breakdown, and the
   chat input enables once data is loaded.
5. Ask a question in the chat box.

Ingested data is stored in a local SQLite file (`chuckle.db`), so a previously
uploaded dataset is restored automatically after a page refresh — no need to
re-upload.

### Smoke test

Upload `tests/fixtures/feeds_only.csv` — the sidebar should show `5 rows ingested`.
Ask **"how many feeds in total?"** and expect an answer of `5`.

## Configuration

Configured via environment variables (loaded from `.env`):

| Variable | Required | Default | Description |
|---|---|---|---|
| `OPENROUTER_API_KEY` | Yes | — | API key for [OpenRouter](https://openrouter.ai/), used for all LLM calls. |
| `CHUCKLE_MODEL` | No | `openai/gpt-4o-mini` | OpenRouter model slug used by the agent. |

**Current model:** the agent runs on **`openai/gpt-4o-mini`** by default, accessed
through OpenRouter (`https://openrouter.ai/api/v1`). Override it by setting
`CHUCKLE_MODEL` to any OpenRouter model slug. The model is called at
`temperature=0` with streaming enabled.

## How the agent works

The assistant is a [LangChain](https://www.langchain.com/) tool-calling agent
(`src/agent.py`) backed by a local SQLite database. The flow for a single question:

1. **Ingest** (`src/ingest.py`) — the uploaded Huckleberry CSV is parsed into
   structured event rows covering all 10 event types (Feed, Sleep, Diaper, Bath,
   Tummy time, Story time, Pump, Meds, Growth, Temp).
2. **Store** (`src/db.py`) — rows are written to an `events` table in `chuckle.db`,
   replacing any previous upload.
3. **Prompt assembly** (`src/prompts.py`) — a system prompt is built per request
   from:
   - the agent's role,
   - the database schema and column descriptions,
   - **temporal context** (the current date/time plus named time-of-day periods
     such as `morning`, `night`, `overnight`, so relative references like
     "yesterday" or "last night" resolve correctly),
   - SQL conventions and few-shot question→SQL examples.
4. **Tool calling** — the agent has a single tool, `query_database`, which runs a
   **read-only SQL `SELECT`** against the `events` table and returns the rows as
   JSON. The LLM writes the SQL, inspects the results, and may issue follow-up
   queries (up to `max_iterations=3`).
5. **Answer generation** — the agent responds in plain natural language, beginning
   by stating the time range it used. It is instructed not to mention SQL or the
   database in its answer.

### Streaming and threading

The agent runs in a background daemon thread so the Streamlit UI stays responsive.
A custom callback handler (`_FinalAnswerHandler`) buffers LLM tokens and only flushes
the **final answer** to the UI — tool-call JSON fragments are discarded so they never
leak into the chat. If the agent errors or hits its iteration limit, a single
fallback message ("Sorry, I couldn't answer that from the data.") is shown.

### Safety guard

`run_select` (`src/db.py`) rejects anything that is not a single `SELECT` statement
(stripping SQL comments before validating). See `docs/security_notes.md` for known
limitations of this application-level guard and recommended hardening.

### Observability

LLM calls, tool invocations, and per-query spans are traced with
[Logfire](https://logfire.pydata.dev/) (configured in `app.py`).
