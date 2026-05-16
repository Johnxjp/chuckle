"""System prompt assembly and temporal context for the agent.

TB-1 only ships the minimal `build_system_prompt` used by the walking skeleton.
Temporal context, column descriptions, sample rows, and few-shot examples
arrive in TB-3.
"""

from __future__ import annotations

ROLE_BLOCK = (
    "You are Chuckle, a helpful assistant that answers questions about a baby's "
    "activity data. You have access to a SQLite database via the `query_database` "
    "tool, which runs read-only SQL SELECT statements against an `events` table. "
    "Use it whenever the question requires data; respond in plain natural language "
    "and do not mention SQL or the database in your answer."
)


def build_system_prompt(schema_ddl: str) -> str:
    return f"""{ROLE_BLOCK}

Database schema (live DDL):
{schema_ddl}
"""
