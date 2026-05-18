# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Chuckle** is an AI assistant that answers natural language questions about a baby's activity data exported from the [Huckleberry](https://huckleberry.app/) child monitoring app, supplemented by NHS baby care guidance.

The project is currently in the planning/data stage — no application code exists yet.

## Data

### `huckleberry_data.csv`
The primary data source. CSV columns:

| Column | Description |
|---|---|
| `Type` | Event type: `Feed`, `Diaper`, `Bath`, `Sleep`, etc. |
| `Start` | Event start datetime (`YYYY-MM-DD HH:MM`) |
| `End` | Event end datetime (empty for instantaneous events) |
| `Duration` | Duration string (e.g. `0:09`) |
| `Start Condition` | Feed detail e.g. `00:05R` (5 min right breast) |
| `Start Location` | Feed side e.g. `Breast` |
| `End Condition` | Feed detail e.g. `00:03L` (3 min left breast) |
| `Notes` | Free text e.g. `Poo:small`, `Pee:medium`, stool colour |

### `evaluation_data.json`
Ground-truth query/answer pairs for evaluating the assistant. Currently an empty array `[]`.

Planned record shape (one object per evaluation case):

```json
{
  "question": "How long did she sleep last night?",
  "expected_answer": "8 hours 12 minutes",
  "expected_time_range": "2026-01-04 21:00:00 – 2026-01-05 05:00:00"
}
```

The eval runner (spec §9) is out of scope for now; this file reserves the format.

## Planned Architecture

From `docs/implementation_thoughts.md`, the core system is an **information retrieval + generation pipeline**:

1. **Query understanding** — parse natural language into structured intent (event type, time range, aggregation)
2. **Temporal resolution** — map relative references ("yesterday", "Wednesday", "this week", "in the morning") to absolute datetime ranges using the user's current time
3. **Data retrieval** — query `huckleberry_data.csv` for matching rows
4. **Answer generation** — summarise or directly answer from retrieved rows

### Key query patterns to support
- Point-in-time lookups: "When was the last time Leo fed?"
- Range aggregations: "How many times did Leo wake up this week?"
- Cross-day intent: "When did Leo eat first the next day?"
- Ambiguous time references: "morning" (infer 5–9am), "bedtime" (infer from data patterns)
- Multi-intent queries combining multiple of the above

### Supplementary knowledge
NHS baby care pages (fetched or pre-indexed) provide context for general childcare questions outside the CSV data.
