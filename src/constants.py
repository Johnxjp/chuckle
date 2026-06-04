"""Project-wide constants for the query agent."""

from datetime import datetime

# Fixed "current time" used as the agent's temporal anchor. Overrides the real
# clock so relative questions ("today", "this morning") resolve against a known
# point. Set to None to fall back to datetime.now().
FIXED_NOW = datetime(2026, 5, 8, 12, 0)
