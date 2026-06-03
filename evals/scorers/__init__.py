"""Scorer package: importing it registers every scorer into SCORER_REGISTRY.

Each scorer module registers its scorers via the ``@scorer`` decorator on
import, so they must be imported for the registry to be populated. Importing
them here means any consumer that imports ``evals.scorers`` (or a submodule
such as ``evals.scorers.registry``) gets a fully populated registry.
"""

from evals.scorers import text_scorers, tool_scorers

__all__ = ["text_scorers", "tool_scorers"]
