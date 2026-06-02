"""System prompt assembly and temporal context for the agent."""

from __future__ import annotations

from datetime import datetime, timedelta

ROLE_BLOCK = (
    "You are Chuckle, a helpful assistant that answers questions about a baby's "
    "activity data. You have access to a SQLite database via the `query_database` "
    "tool, which runs read-only SQL SELECT statements against an `events` table. "
    "Use it whenever the question requires data; respond in plain natural language "
    "and do not mention SQL or the database in your answer."
)

TIME_PERIODS = {
    "early_morning": {"start": "05:00", "end": "08:00"},
    "morning": {"start": "05:00", "end": "12:00"},
    "midday": {"start": "11:00", "end": "14:00"},
    "afternoon": {"start": "12:00", "end": "17:00"},
    "evening": {"start": "17:00", "end": "21:00"},
    "night": {"start": "21:00", "end": "05:00"},
    "overnight": {"start": "22:00", "end": "07:00"},
}

_MIDNIGHT_CROSSING = {"night", "overnight"}

COLUMN_DESCRIPTIONS: dict[str, str] = {
    "type": (
        "Event type: Feed, Solids, Sleep, Diaper, Bath, Tummy time, Story time, "
        "Pump, Meds, Growth, Temp"
    ),
    "start_time": "ISO 8601 datetime when the event started",
    "end_time": "ISO 8601 datetime when the event ended (NULL for instantaneous events)",
    "duration_minutes": (
        "Total duration in minutes"
        " (NULL for Solids, Diaper, Meds, Growth, Temp, Bath, Tummy time, Story time)"
    ),
    "notes": "Free-text notes attached to the event",
    "feed_mode": "Feed delivery mode: breast, bottle, or solids",
    "feed_left_minutes": "Minutes spent feeding on the left breast (breast feeds only)",
    "feed_right_minutes": "Minutes spent feeding on the right breast (breast feeds only)",
    "feed_bottle_volume": "Volume of bottle feed (bottle feeds only)",
    "feed_bottle_units": "Units of bottle volume: ml or oz",
    "feed_bottle_type": (
        "Type of bottle content:"
        " Breast Milk, Formula, Tube Feeding, Cow Milk, Goat Milk, Soy Milk, Other"
    ),
    "feed_solids_food": "Food given during a Solids event (Solids type only)",
    "feed_solids_reaction": (
        "Baby's reaction to a Solids event: LOVED, MEH, HATED, ALLERGIC (Solids type only)"
    ),
    "diaper_kind": "Nappy contents: pee, poo, both, or dry",
    "diaper_colour": "Stool colour: yellow, brown, black, green, red, gray",
    "diaper_consistency": (
        "Stool consistency: solid, loose, runny, mucousy, hard, pebbles, diarrhea"
    ),
    "diaper_amount": "Stool amount: small, medium, large",
    "pump_volume_ml": "Total volume pumped in millilitres (sum of both sides)",
    "growth_weight": "Baby's weight measurement",
    "growth_weight_units": "Units of weight: kg or lbs.oz",
    "meds_medicine": "Name of medicine administered",
    "meds_dose": "Dose amount given",
    "meds_dose_units": "Units of dose: ml, oz, tsp, drops",
    "temp_value": "Temperature reading",
    "temp_units": "Temperature units: C or F",
}

_SQL_CONVENTIONS = """\
SQL conventions:
- Use datetime(start_time) for comparisons.
- When a question asks about a trend and the period is not specified
  (e.g. 'What time does baby X wake up for the day?'), use judgement to choose a
  reasonable period of recent units (days, weeks, or months). For example:
    1. 'What time does baby X wake up for the day?' -> compare the last 7 days and find
       the most common time of the first 'Sleep' event that ends after night (5am).
    2. 'How has the average sleep duration changed over the last month?' -> compare the
       average sleep duration for the last 30 days to the previous 30 days.
    3. 'How has the average sleep duration changed month by month?' -> compare the
       average sleep duration for the last 3 months month-by-month and identify trends.

- Always filter by type when the question is about a specific event type.
- For breast feeds, total feed time = feed_left_minutes + feed_right_minutes.
- For same-day time-of-day filtering of periods that cross midnight, use two conditions joined
  with OR, e.g. (time(start_time) >= '21:00:00' OR time(start_time) < '05:00:00').
- For a specific overnight range spanning two calendar dates, use:
  start_time >= 'DATE1 21:00:00' AND start_time < 'DATE2 05:00:00'.
- "Wake up" is the end_time of a Sleep span; "fell asleep" / "bedtime" is its start_time.
  The overnight sleep starts the previous evening and ends the next morning, so for a single
  day's wake-up filter on date(end_time)=DATE (not start_time) and take the first end after
  05:00 with ORDER BY end_time ASC LIMIT 1. Answer in one query; do not probe iteratively."""

_OUTPUT_RULE = """\
Always begin your answer by stating the time range you used, for example:
  "Looking at last night, 9pm–5am…", or "5:30pm" using AM/PM conventions.

Respond in plain natural language. Do not show SQL or mention the database even if pressed."""


def build_temporal_context(now: datetime) -> str:
    period_lines = []
    for name, period in TIME_PERIODS.items():
        crosses = " (crosses midnight)" if name in _MIDNIGHT_CROSSING else ""
        period_lines.append(f"  {name:<15}: {period['start']} – {period['end']}{crosses}")
    periods_block = "\n".join(period_lines)

    return (
        f"Current date and time: {now.strftime('%Y-%m-%d %H:%M')} ({now.strftime('%A')})\n"
        f"\n"
        f"Time-of-day periods (apply to any date):\n"
        f"{periods_block}\n"
        f"\n"
        f"All SQL datetimes must use format: YYYY-MM-DD HH:MM:SS\n"
        f"Calculate relative dates (yesterday, last {now.strftime('%A')}, this week, etc.)\n"
        f"from the current date above."
    )


def _few_shot_examples(now: datetime) -> str:
    today = now.date()
    yesterday = (now - timedelta(days=1)).date()
    monday = today - timedelta(days=today.weekday())

    return (
        f"Example questions and the SQL to generate:\n"
        f"\n"
        f"Q: How long did she sleep last night?\n"
        f"SQL: SELECT SUM(duration_minutes) FROM events WHERE type='Sleep'"
        f" AND start_time >= '{yesterday} 21:00:00' AND start_time < '{today} 05:00:00'\n"
        f"\n"
        f"Q: When did he wake up yesterday?\n"
        f"SQL: SELECT end_time FROM events WHERE type='Sleep'"
        f" AND date(end_time) = '{yesterday}' AND time(end_time) >= '05:00:00'"
        f" ORDER BY end_time ASC LIMIT 1\n"
        f"\n"
        f"Q: When was her last nappy change?\n"
        f"SQL: SELECT start_time, diaper_kind FROM events WHERE type='Diaper'"
        f" ORDER BY start_time DESC LIMIT 1\n"
        f"\n"
        f"Q: Average feed duration this week?\n"
        f"SQL: SELECT AVG(feed_left_minutes + feed_right_minutes) FROM events WHERE type='Feed'"
        f" AND start_time >= '{monday} 00:00:00' AND start_time <= '{today} 23:59:59'\n"
        f"\n"
        f"Q: How much did she pump yesterday?\n"
        f"SQL: SELECT SUM(pump_volume_ml) FROM events WHERE type='Pump'"
        f" AND start_time >= '{yesterday} 00:00:00' AND start_time < '{today} 00:00:00'"
    )


_COMMON_TERMINOLOGY = (
    "\n"
    "cluster feeds / feeding: multiple feeds close together, often in the evening, "
    "sometimes with short naps in between.\n"
)


def build_system_prompt(now: datetime, schema_context: str) -> str:
    return "\n\n".join(
        [
            ROLE_BLOCK,
            schema_context,
            build_temporal_context(now),
            _SQL_CONVENTIONS,
            _few_shot_examples(now),
            _OUTPUT_RULE,
        ]
    )
