# Test Suite Redundancy Review

Reviewed all test files under `tests/` (1129 lines of test code covering ~1450 lines of production code in `src/` and `app.py`).

`uv run pytest --collect-only` reports **150 test IDs** across ~90 test functions — the difference is parametrised cases, each of which pytest counts as its own test (e.g. `test_run_select_rejects` alone expands to 14).

The verdict: the suite is broadly well-targeted, but there is a clear class of "wrote-the-string, asserted-the-string" tests and several over-parametrisations of the same underlying code path. A minimum-viable cut would remove roughly **55–60 of the 150 collected test IDs** without meaningful coverage loss.

The matrix below uses three categories:

- **REMOVE** — duplicate coverage of a path another test already exercises, or testing the language/framework rather than our code.
- **MERGE** — two or more tests that should collapse into one (parametrise or combine asserts).
- **KEEP** — load-bearing.

---

## `tests/test_prompts.py` — biggest cull target

`build_system_prompt` is `"\n\n".join([...])` over six largely-static blocks. Most tests here assert that a hard-coded substring you typed into the template is present in the rendered string. They will only fail if someone deliberately deletes a line from the template — they don't catch real bugs.

| Test | Verdict | Reason |
|---|---|---|
| `test_temporal_context_contains_date_and_time` | MERGE | Substring check on literal template content. |
| `test_temporal_context_contains_day_of_week` | MERGE | Same. |
| `test_temporal_context_contains_all_periods` | MERGE | Iterates `TIME_PERIODS` and asserts each name appears — but the template `f"{name:<15}: ..."` literally interpolates them; tautological. |
| `test_temporal_context_midnight_crossing_annotated` | MERGE | Same shape. |
| `test_temporal_context_datetime_format_instruction` | MERGE | Same shape. |
| `test_temporal_context_changes_with_different_now` | **KEEP** | The only one that actually exercises the parametrised behaviour (date varies with `now`). |
| `test_build_system_prompt_contains_role` | REMOVE | "Chuckle" appears in `ROLE_BLOCK` because we wrote it there. |
| `test_build_system_prompt_contains_schema_context` | REMOVE | Asserts the literal string passed in as an argument is returned in the output. |
| `test_build_system_prompt_contains_temporal_block` | REMOVE | Duplicate of `temporal_context_*` tests, just via the wrapper. |
| `test_build_system_prompt_contains_few_shot_examples` | REMOVE | Substring of literal template text. |
| `test_build_system_prompt_contains_output_rule` | REMOVE | Same. |
| `test_build_system_prompt_few_shot_dates_derived_from_now` | **KEEP** | Verifies the dynamic date arithmetic in `_few_shot_examples`. |

**Minimum viable: 2 tests** (one for the date-derivation in `temporal_context`, one for the few-shot date logic). Everything else is a snapshot test of strings you can read in the source.

---

## `tests/test_db_security.py` — over-parametrised

The 14-entry parametrised list has high redundancy because the guard works in two simple steps: strip comments → check `startswith("select")` → check no second statement. Each branch needs *one* example, not three or four.

| Test | Verdict | Reason |
|---|---|---|
| `test_run_select_rejects[INSERT…]` | **KEEP** | Representative direct mutation. |
| `test_run_select_rejects[UPDATE…]` | REMOVE | Same code path as INSERT (doesn't start with `select`). |
| `test_run_select_rejects[DELETE FROM events]` | REMOVE | Same. |
| `test_run_select_rejects[DROP TABLE events]` | REMOVE | Same. |
| `test_run_select_rejects[ATTACH DATABASE…]` | REMOVE | Same. |
| `test_run_select_rejects[PRAGMA…]` | REMOVE | Same. |
| `test_run_select_rejects[SELECT…; DROP…]` | **KEEP** | Exercises the multi-statement regex branch. |
| `test_run_select_rejects[SELECT 1; SELECT 2]` | REMOVE | Same branch as above. |
| `test_run_select_rejects[SELECT…; INSERT…]` | REMOVE | Same branch. |
| `test_run_select_rejects[-- comment\nDELETE]` | **KEEP** | Exercises the line-comment stripping. |
| `test_run_select_rejects[--…\n--…\nINSERT]` | REMOVE | Same branch. |
| `test_run_select_rejects[/* comment */ INSERT]` | **KEEP** | Exercises the block-comment branch. |
| `test_run_select_rejects[/* multi\nline */ UPDATE]` | REMOVE | Same branch (the `re.DOTALL` is the only difference and that's not what's under test). |
| `test_run_select_rejects[SELECT…; /* */ DROP]` | REMOVE | Combination of branches already covered individually. |
| `test_run_select_allows_plain_select` | **KEEP** | Happy path. |
| `test_run_select_allows_select_with_inline_comment` | MERGE | All three "allows…" tests should be one parametrised happy-path test. |
| `test_run_select_allows_select_with_trailing_semicolon_only` | MERGE | Same. |
| `test_run_select_allows_select_with_leading_whitespace` | MERGE | Same. |
| `test_get_schema_context_contains_ddl` | MERGE | Three tests, one helper. Collapse into a single test with three asserts. |
| `test_get_schema_context_contains_column_descriptions` | MERGE | Same. |
| `test_get_schema_context_no_data_placeholder` | MERGE | Same. |

**Minimum viable: ~5 tests** (3 reject branches, 1 allow happy-path parametrised, 1 schema context).

---

## `tests/test_agent_streaming.py` — heavy duplication around SELECT-only

The `_FinalAnswerHandler` tests and the cross-thread regression are valuable. The "query_database tool" tests are mostly re-running `test_db_security.py` through the tool wrapper, and the `TestAgentWithFakeLLM` `select_only` test re-runs it *again* through the executor.

| Test | Verdict | Reason |
|---|---|---|
| `TestFinalAnswerHandler.test_emits_tokens_for_plain_text_response` | **KEEP** | Core handler behaviour. |
| `TestFinalAnswerHandler.test_discards_tokens_for_tool_call_response` | **KEEP** | Core inverse case. |
| `TestFinalAnswerHandler.test_clears_buffer_on_llm_error` | **KEEP** | Failure mode. |
| `TestFinalAnswerHandler.test_two_rounds_only_emits_final_answer_tokens` | **KEEP** | Multi-round case is the realistic shape. |
| `TestAnswerGenerator.test_answer_is_a_generator` | REMOVE | Asserts `hasattr(gen, "__next__")` — testing Python language, not our code. |
| `TestAnswerGenerator.test_cross_thread_connection_does_not_raise` | **KEEP** | Real regression test for a real prior bug (per the commit log). |
| `TestAnswerGenerator.test_yields_fallback_on_build_exception` | **KEEP** | One of the three fallback paths. |
| `TestAnswerGenerator.test_yields_fallback_when_agent_stopped` | MERGE | All three fallback tests hit the same `q.put(_FALLBACK)` plumbing; one is enough or parametrise. |
| `TestAnswerGenerator.test_yields_fallback_on_empty_output` | MERGE | Same plumbing. |
| `TestQueryDatabaseTool.test_valid_select_returns_json_array` | **KEEP** | Tool wrapper happy path. |
| `TestQueryDatabaseTool.test_empty_table_returns_empty_array` | REMOVE | Tests `json.dumps([])`, which is stdlib. |
| `TestQueryDatabaseTool.test_drop_returns_error_string_not_raises` | **KEEP** | One example that the ValueError → "ERROR:" wrapping works. |
| `TestQueryDatabaseTool.test_insert_returns_error_string` | REMOVE | Same wrapping path as DROP. |
| `TestQueryDatabaseTool.test_multi_statement_returns_error_string` | REMOVE | Same wrapping path. |
| `TestAgentWithFakeLLM.test_tool_invoked_and_final_string_produced` | **KEEP** | Best end-to-end check in the whole agent suite. |
| `TestAgentWithFakeLLM.test_select_only_enforced_in_agent_flow` | REMOVE | Third time we test SELECT-only (db, tool, agent). The agent layer doesn't add anything to the enforcement. |

---

## `tests/test_ingest.py` — mostly justified, some duplication

This file is the most defensible — CSV parsing is genuinely fiddly and each parser branch is real code worth covering. But several pass-through types and minor variants are redundant.

| Test | Verdict | Reason |
|---|---|---|
| All `test_parse_feed_*` | **KEEP** | Three meaningfully different code paths (breast, bottle no-type, bottle with-type). |
| `test_parse_sleep_duration` | **KEEP** | |
| `test_parse_sleep_short_duration` | REMOVE | Same code path as `test_parse_sleep_duration`, just different numbers. |
| `test_parse_diaper_*` (5 tests) | **KEEP** | Each exercises a distinct regex branch / validation rule (kind, amount, invalid colour nulled, consistency variants, no-amount). |
| `test_parse_pump_sums_both_sides` | **KEEP** | Sum logic. |
| `test_parse_pump_single_side` | **KEEP** | `or 0` branch. |
| `test_parse_pump_oz_converted_to_ml` | **KEEP** | Unit conversion. |
| `test_parse_pump_zero_volume` | REMOVE | Edge case that the existing tests cover implicitly; the production code's `(vol_a or 0) + (vol_b or 0)` handles 0 the same as any positive int. |
| `test_parse_meds_with_dose` / `test_parse_meds_no_dose` | **KEEP** | Distinct paths. |
| `test_parse_growth_kg` | **KEEP** | |
| `test_parse_growth_whole_number` | REMOVE | The `(?:\.\d+)?` regex makes whole numbers and decimals the same path. |
| `test_parse_temp_celsius` | **KEEP** | Single representative. |
| `test_parse_bath` | **KEEP** | Representative for the "simple event type" pass-through. |
| `test_parse_tummy_time` | REMOVE | Same pass-through code path as Bath. |
| `test_parse_story_time` | REMOVE | Same. |
| Validation tests (4) | **KEEP** | Each is a distinct branch of `_parse_row`. |
| `test_full_csv_type_counts` | **KEEP** | Best integration anchor we have. |
| `test_full_csv_re_parse_is_idempotent` | REMOVE | Parser has no random/global state; this asserts determinism, which is given. |

---

## `tests/scraper/test_db.py` — testing SQLite

Several of these are unit tests of SQLite itself (CHECK constraints, FK enforcement), not our code. They're cheap, but they aren't catching bugs in our logic.

| Test | Verdict | Reason |
|---|---|---|
| `test_init_schema_creates_tables` | REMOVE | Asserts `CREATE TABLE` creates a table. |
| `test_insert_pending_dedupes` | **KEEP** | Tests the `ON CONFLICT … DO NOTHING` behaviour. |
| `test_check_constraint_rejects_invalid_status` | MERGE | Worth one CHECK-constraint test as a smoke that the schema was applied; doing it for both `status` and `classification` is unit-testing SQLite. |
| `test_check_constraint_rejects_invalid_classification` | MERGE | See above. |
| `test_foreign_key_rejects_orphan_scraped_content` | REMOVE | Unit-tests SQLite FK enforcement (after confirming pragma is on). Low value once the merged CHECK test exists. |
| `test_upsert_refreshes_content` | **KEEP** | Tests the `ON CONFLICT … DO UPDATE` semantics. |
| `test_mark_processed_updates_fields` | MERGE | Three "mark_*" tests of identical shape — parametrise to one. |
| `test_mark_failed_sets_reason` | MERGE | Same. |
| `test_mark_redirected_records_target` | MERGE | Same. |
| `test_reset_failed_to_pending` | REMOVE | Subset of `reset_all_for_force`'s behaviour. |
| `test_reset_all_for_force_skips_blocked` | **KEEP** | The skip-blocked branch is the only non-trivial part of the reset functions. |
| `test_next_pending_returns_oldest_first` | **KEEP** | Validates the ORDER BY in next_pending. |

---

## `tests/scraper/test_normaliser.py` — already well-condensed

| Test | Verdict | Reason |
|---|---|---|
| `test_normalise_rules` (9 cases) | **KEEP** | Each row is a distinct rule. |
| `test_normalise_rejects_invalid` (7 cases) | MERGE | `""` and `"   "` are the same case; `mailto:` / `tel:` / `javascript:` all hit the rejected-schemes set the same way — one representative is enough. Could drop to 4 cases. |

---

## `tests/scraper/test_scope.py` — borderline-cheap

| Test | Verdict | Reason |
|---|---|---|
| `test_in_scope_accepts` (parametrised) | **KEEP** | Distinct acceptance paths. |
| `test_in_scope_rejects_some` | **KEEP** | Has an odd in-test `if` branch — should be refactored into separate cases rather than removed. |
| `test_is_exception_url_true` | MERGE | Two-line function; both tests collapse to one parametrised. |
| `test_is_exception_url_false` | MERGE | Same. |

---

## `tests/scraper/test_fetcher.py` — minor overlap

| Test | Verdict | Reason |
|---|---|---|
| `test_fetch_ok_returns_body` | **KEEP** | |
| `test_fetch_4xx_fails_immediately` | **KEEP** | No-retry path. |
| `test_fetch_5xx_retries_then_fails` | **KEEP** | Retry exhaustion. |
| `test_fetch_429_retries_then_recovers` | **KEEP** | Retry + recovery. |
| `test_fetch_redirect_returns_target` | **KEEP** | |
| `test_fetch_timeout_retries` | REMOVE | Same code path as `test_fetch_5xx_retries_then_fails` (retry exhaustion); the only distinction is the failure_reason string, which doesn't change behaviour. |
| `test_fetch_rejects_non_html_content_type` | **KEEP** | |
| `test_fetch_rejects_oversized_response` | **KEEP** | |

---

## `tests/scraper/test_crawler.py` — keep nearly all

These are integration-shaped and each exercises a distinct branch of the BFS loop.

| Test | Verdict |
|---|---|
| `test_extract_links_returns_nine_baby_subtopics` | **KEEP** |
| `test_extract_links_dedupes_and_normalises` | **KEEP** |
| `test_bfs_loop_orders_breadth_first_and_respects_cap` | **KEEP** |
| `test_max_pages_cap_stops_processing` | REMOVE — the previous test already asserts `stats.processed == 3` against a `max_pages=10` cap, and trimming `max_pages=1` is a one-line variation. Could be folded into the BFS test as parametrised cases. |
| `test_exception_url_skips_link_extraction` | **KEEP** |
| `test_robots_blocked_url_marked_and_not_fetched` | **KEEP** |
| `test_in_scope_redirect_creates_target_row` | **KEEP** |
| `test_out_of_scope_redirect_drops_body` | **KEEP** |
| `test_failed_url_records_attempt_count` | **KEEP** |

---

## `tests/scraper/test_classifier.py` — keep all

Each test covers a distinct heuristic branch (real fixtures, empty main, prose-dominant, link-dominant, short-paragraph + link-dominant, heading + prose + links). No redundancy worth pulling.

---

## `tests/test_app_smoke.py` — keep all

Four tests, each a different Streamlit wiring concern (disabled-before-upload, single-upload success, idempotent re-upload, empty-CSV warning, agent error swallowed). Tightest file in the suite.

---

## Summary — minimum-viable cut

If forced to strip aggressively, the suite would drop from **150 collected test IDs to ~90** (or ~55 distinct test functions after merges) by removing/merging:

**REMOVE outright (≈20):**

- `test_prompts.py`: all `build_system_prompt_contains_*` (5 tests) and the temporal-context substring checks (5 of 6).
- `test_db_security.py`: 9 redundant parametrised `rejects` cases.
- `test_agent_streaming.py`: `test_answer_is_a_generator`, two of three fallback tests, `test_empty_table_returns_empty_array`, two of three "tool returns error string" tests, `test_select_only_enforced_in_agent_flow`.
- `test_ingest.py`: `test_parse_sleep_short_duration`, `test_parse_pump_zero_volume`, `test_parse_growth_whole_number`, `test_parse_tummy_time`, `test_parse_story_time`, `test_full_csv_re_parse_is_idempotent`.
- `tests/scraper/test_db.py`: `test_init_schema_creates_tables`, `test_foreign_key_rejects_orphan_scraped_content`, `test_reset_failed_to_pending`.
- `tests/scraper/test_fetcher.py`: `test_fetch_timeout_retries`.
- `tests/scraper/test_crawler.py`: `test_max_pages_cap_stops_processing`.

**MERGE into parametrised tests (≈15 → ≈5):**

- Three "allows SELECT with X" tests → one.
- Three `get_schema_context_*` substring tests → one.
- Two CHECK-constraint tests → one.
- Three `mark_*` tests → one parametrised.
- Two `is_exception_url_*` tests → one parametrised.

**The principle**: keep tests that exercise *branches you wrote* (regex alternations, retry exhaustion, fallback paths, CHECK constraints once). Drop tests that assert string literals you typed into a template, or that re-test the same code path through a wrapper layer.

The two regression-style tests are the most load-bearing items in the whole suite and would survive any cut:

- `test_cross_thread_connection_does_not_raise` — locks in commit `6ed5aaa`'s fix.
- `test_full_csv_type_counts` — only check that the parser handles the real export end-to-end.
