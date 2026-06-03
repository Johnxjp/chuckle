from collections.abc import Callable

SCORER_REGISTRY: dict[str, Callable] = {}


def scorer(name: str):
    """Decorator to register a scorer."""

    def decorator(fn):
        SCORER_REGISTRY[name] = fn
        return fn

    return decorator
