# Chuckle Implementation Plan

This plan turns `spec.md` into independently workable tickets. Work is organised into **tracer bullets** — thin vertical slices that go end-to-end (UI → agent → SQL → DB → CSV) and prove the system holds together — followed by widening tickets that thicken each layer. Each ticket lists explicit dependencies on others; tickets without dependencies can be worked in parallel.

## Legend
- **TB-n** = Tracer bullet milestone (vertical slice; should be demoable end-to-end).
- **T-n** = Standalone ticket. Can usually be parallelised once its deps land.
- **Deps:** lists ticket IDs that must be merged first.
- **DoD** = Definition of Done.

---

## TB-0 — Project scaffold
**Goal:** Empty repo becomes a runnable Python project with deps wired.
**Deps:** none.

### T-0.1 Repo skeleton
- `pyproject.toml` (Python 3.11+), `.gitignore` (`chuckle.db`, `.env`, `__pycache__`), `.env.example` with `OPENROUTER_API_KEY` and `CHUCKLE_MODEL`.
- Empty module files matching Section 10 of spec: `app.py`, `ingest.py`, `agent.py`, `db.py`, `prompts.py`.
- `tests/` directory with `conftest.py` placeholder.
- **DoD:** `python -m streamlit run app.py` launches an empty page; `pytest` runs zero tests successfully.

### T-0.2 Dependency set
- Add: `streamlit`, `pandas` (parsing), `langchain`, `langchain-openai`, `pydantic`, `python-dotenv`, `pytest`.
- Pin majors; commit lockfile if using `uv`/`poetry`.
- **DoD:** Fresh checkout → `uv sync` (or equivalent) → app starts.

---

## TB-1 — Walking skeleton (end-to-end on a single event type)
**Goal:** Prove every layer talks to the next, with the narrowest possible content: upload a CSV containing only `Feed` rows, ask one canned question ("how many feeds in total?"), get a streamed LLM answer derived from real SQL. Everything else (other types, temporal reasoning, streaming polish, error swallowing) is faked or stubbed. This is the most important ticket — it de-risks integration before any layer is built out.
**Deps:** TB-0.

### T-1.1 Minimal `db.py`
- `init_db(path)` creates the `events` table (full schema from spec §4.1; CHECK constraints in place from day one — cheaper than retrofitting).
- `replace_events(conn, events)` — single transaction, `DELETE FROM events` then `executemany INSERT`.
- `run_select(conn, sql)` — naive SELECT-only guard (strip + lowercase + startswith `select`); thorough enforcement deferred to T-3.2.
- **Deps:** T-0.1.

### T-1.2 Minimal `ingest.parse_csv` (Feed only)
- Parses `Type=Feed` rows, extracts `start_time`, `end_time`, `duration_minutes`, `feed_mode`, `feed_left_minutes`, `feed_right_minutes`. Skips every other type.
- Returns `list[dict]` matching the `events` columns (NULLs for the rest).
- **Deps:** T-1.1.

### T-1.3 Minimal `agent.py`
- `build_agent(now)` constructs a LangChain ReAct/tool-calling agent with one tool `query_database` wrapping `db.run_select`.
- System prompt is hardcoded: role + live DDL only (no temporal block, no few-shot — those land in TB-3).
- `answer(question, now)` returns a string (not a generator yet).
- ChatOpenAI configured with OpenRouter `base_url` and env-var key.
- **Deps:** T-1.1.

### T-1.4 Minimal Streamlit `app.py`
- Sidebar `st.file_uploader` → on submit, call `ingest.parse_csv` + `db.replace_events`, show a single row count.
- Main pane: `st.chat_input` → call `agent.answer` → `st.write` (no streaming yet).
- Chat input gated on `st.session_state.db_ready`.
- **Deps:** T-1.2, T-1.3.

### T-1.5 Smoke-test fixture
- A 5-row hand-crafted CSV (all Feed) committed under `tests/fixtures/feeds_only.csv`.
- Manual test script in README: "upload this, ask 'how many feeds?', expect 5".
- **Deps:** T-1.4.

**TB-1 DoD:** Real OpenRouter call, real SQLite file, real Streamlit, returns a grounded answer to one question. Demoable.

---

## TB-2 — Full ingestion fidelity
**Goal:** All ten event types parse correctly from the real `huckleberry_data.csv`; ingestion summary surfaces per-type counts and warnings. The agent benefits automatically (no agent changes needed).
**Deps:** TB-1.

### T-2.1 Per-type parsers (parallelisable sub-tickets)
Each parser is an isolated function in `ingest.py`. All can be worked in parallel by different contributors; merge order doesn't matter.

- **T-2.1a Sleep** — `Duration` `H:MM` → minutes.
- **T-2.1b Diaper** — `diaper_colour` ← `Duration`; `diaper_consistency` ← `Start Condition`; `diaper_kind` + `diaper_amount` ← `End Condition` (`Poo:small`).
- **T-2.1c Pump** — `pump_volume_ml` ← sum of `Start Condition` + `End Condition` (`30ml`/`0ml`); convert oz → ml.
- **T-2.1d Meds** — `meds_medicine` ← `Start Location`; `meds_dose` ← `Notes` (bare number); units NULL if absent.
- **T-2.1e Growth** — `growth_weight` + `growth_weight_units` ← `Start Condition` (`6.6kg`).
- **T-2.1f Temp** — `temp_value` + `temp_units` ← `Start Condition` (`37.4°C`).
- **T-2.1g Bath / Tummy time / Story time** — `notes` only.
- **Deps (each):** T-1.2 (so the dispatch skeleton exists).

### T-2.2 Validation + warning collection
- Implement Section 4.4 rules: unknown `type` → skip; missing `start_time` → skip; `end_time < start_time` → log + insert; CHECK violations → NULL the column.
- Return `ParseResult(events, warnings)` from `parse_csv`.
- **Deps:** T-2.1a–g.

### T-2.3 Ingestion summary UI
- Sidebar shows row count per `type` after upload (replaces the single-count placeholder from T-1.4).
- Collapsible "Warnings" expander listing skipped rows / anomalies.
- **Deps:** T-2.2.

**TB-2 DoD:** Full `huckleberry_data.csv` ingests; per-type counts match the documented distribution (Feed 1295, Diaper 1715, Sleep 764, Pump 205, Bath 76, Tummy time 18, Meds 15, Growth 3, Story time 6, Temp 1). Re-uploading the same CSV produces identical row counts.

---

## TB-3 — Temporal reasoning and prompt quality
**Goal:** Agent correctly resolves "last night", "yesterday", "this week", "this morning". This is the slice that turns the assistant from a SQL-runner into a useful chat partner. Independent of TB-2 in principle — could run in parallel — but easier to evaluate against the real dataset, so listed after.
**Deps:** TB-1 (TB-2 not strictly required but recommended for realistic evaluation).

### T-3.1 `prompts.build_temporal_context(now)`
- Pure function: `strftime` of `now` + the `TIME_PERIODS` block (verbatim from spec §6.1).
- No date arithmetic — the LLM does the math; we just hand it the constants.
- Unit-testable in isolation (frozen `now`).
- **Deps:** T-0.1.

### T-3.2 Hardened SELECT-only guard in `db.run_select`
- Reject: `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ATTACH`, `PRAGMA`, multi-statement strings, SELECT followed by `;` + another statement, leading comments hiding a mutation.
- Raise `ValueError("only SELECT statements are allowed")`; error is caught inside the tool and surfaced to the agent as a retryable string.
- **Deps:** T-1.1. Parallelisable with T-3.1 / T-3.3.

### T-3.3 Schema context function `db.get_schema_context(conn)`
- Assembles: live DDL from `sqlite_master` + column-description dict from `prompts.py` + 3 sample rows per type from the live DB.
- **Deps:** T-1.1, T-2.2 (sample rows are richer with all types present).

### T-3.4 Full system prompt assembly
- `prompts.build_system_prompt(now, schema_context)` produces the 8-section prompt from spec §6.2 (role, schema, column descriptions, samples, temporal block, SQL conventions, few-shot Q→SQL, output rule).
- Wire into `agent.build_agent`, replacing the placeholder prompt from T-1.3.
- **Deps:** T-3.1, T-3.3.

### T-3.5 Tool-call cap + final-failure message
- Agent executor configured with `max_iterations=3`.
- On exhaustion, return the fixed string "Sorry, I couldn't answer that from the data."
- **Deps:** T-1.3.

**TB-3 DoD:** "How long did she sleep last night?" returns the right number with the time range stated in the answer prefix ("Looking at last night, 21:00–05:00…"). "Average feed duration this week?" works. Asking the same question at different times of day (mocked `now`) produces correctly shifted ranges.

---

## TB-4 — UX polish (streaming, status, error-swallowing)
**Goal:** The chat feels alive; no agent internals leak. This is what makes the MVP usable beyond a demo.
**Deps:** TB-1; ideally TB-3 so real answers are worth streaming.

### T-4.1 Streaming `agent.answer`
- Convert `answer` to `Iterator[str]` yielding tokens of the final assistant message only.
- Use LangChain's streaming callback handler; filter out tool-call / intermediate-step events.
- **Deps:** T-1.3.

### T-4.2 `st.write_stream` integration
- Replace `st.write` with `st.write_stream(agent.answer(...))` in `app.py`.
- **Deps:** T-4.1, T-1.4.

### T-4.3 Status indicator
- `st.status("Searching your data...", state="running")` rendered above the assistant message placeholder; transitions to collapsed on first streamed token.
- No raw SQL, no error strings, no retry counts surfaced.
- **Deps:** T-4.2.

### T-4.4 Session state and transcript persistence
- `st.session_state.messages` holds the visible transcript across reruns.
- `st.session_state.db_ready` flips true on first successful ingest, gates the chat input.
- **Deps:** T-1.4.

### T-4.5 Error swallowing
- Wrap the agent call in `app.py`; on uncaught exception render the fixed "Sorry…" message and log full trace server-side.
- **Deps:** T-4.2, T-3.5.

**TB-4 DoD:** First token within 3s on a typical question; tool errors never visible; transcript survives reruns; chat disabled until ingest.

---

## TB-5 — Test suite and evaluation reservation
**Goal:** Lock in correctness so future changes don't regress. Mostly parallel to TB-2–TB-4 but listed last because it audits the whole stack.
**Deps:** TB-2 for ingestion tests; TB-3 for agent tests.

### T-5.1 `ingest.parse_csv` unit tests
- Hand-built fixture CSV with at least one row per type, plus edge cases: missing `End`, non-numeric `Duration` for Diaper, multi-side feed encoding, CHECK violations, malformed rows.
- Assert exact `EventRecord` output.
- **Deps:** TB-2.

### T-5.2 Field-parser unit tests
- Duration `H:MM`, feed-side encoding, pump-volume oz→ml, growth-weight unit strip, temp-value unit strip — all tested in isolation.
- **Deps:** T-2.1a–g (each parser can land its own tests).

### T-5.3 `db.run_select` security tests
- Parametrised tests covering the rejection list in T-3.2.
- **Deps:** T-3.2.

### T-5.4 End-to-end ingestion integration test
- Load real `huckleberry_data.csv`, assert per-type counts from spec §8.2.
- **Deps:** TB-2.

### T-5.5 Agent harness with stub LLM
- LangChain `FakeListLLM` or equivalent emitting canned tool calls; assert tool invoked, SELECT-only enforced, final string produced.
- **Deps:** TB-3.

### T-5.6 `evaluation_data.json` schema reservation
- Document the JSON shape (out of scope to *build* the eval runner — see spec §9 — but the file format is reserved). Commit an empty array with a short README block describing the planned `{question, expected_answer, expected_time_range}` shape.
- **Deps:** none.

---

## Dependency graph (high level)

```
TB-0 ──► TB-1 ──► TB-2 ──► TB-3 ──► TB-4
                    │         │
                    └────► TB-5 ◄┘
```

TB-3 and TB-4 can overlap once TB-1 is in. T-3.1 / T-3.2 / T-3.3 inside TB-3 are mutually independent. T-2.1a–g inside TB-2 are mutually independent. Test tickets (T-5.x) shadow each ticket whose surface they cover.

## Parallelisation guidance

If two engineers are available after TB-1:
- Engineer A: TB-2 (ingestion widening).
- Engineer B: TB-3 (temporal + prompt) using the Feed-only data already in the DB.

If three:
- Engineer C: TB-4 streaming/UX, scaffolded against the TB-1 agent and lit up properly once TB-3 lands.

## Risks and watch-outs

- **OpenRouter streaming through LangChain**: validate that the streaming callback only emits final-message tokens — easy to accidentally stream tool-call deltas into the UI. Pin this down in TB-1 if possible; otherwise it bites in T-4.1.
- **CHECK constraint vs. real data**: the spec's enum lists are derived from sample data. First full-CSV ingest in TB-2 will reveal stragglers (unexpected diaper colour, etc.). Validation rule (NULL the column, don't fail the insert) is the safety net — make sure T-2.2 lands before TB-2 DoD assertion.
- **Temporal correctness is the hardest correctness question**: the LLM does the date math. Spend evaluation effort on TB-3 DoD, not just "it returned something". Vary the mocked `now` across day/week/month boundaries.
