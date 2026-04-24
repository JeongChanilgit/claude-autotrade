"""Strategy registry — self-registration + lookup for backtest strategies.

Strategies self-register at import time. The backtest API and frontend use
this registry as the single source of truth for which strategies exist, what
params they accept, and how to instantiate them.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Callable, Literal

from kairos.backtest.protocols import Strategy


@dataclass(frozen=True, slots=True)
class ParamField:
    """UI-form descriptor for a single strategy parameter."""

    type: Literal["int", "float", "bool", "string", "enum"]
    default: object
    min: float | None = None
    max: float | None = None
    # Enum choices are only meaningful when type == "enum".
    choices: tuple[str, ...] | None = None
    help: str = ""


@dataclass(frozen=True, slots=True)
class StrategyEntry:
    """A registry row — everything needed to describe and build a strategy."""

    name: str
    label: str
    factory: Callable[[], Strategy]
    default_params: Mapping[str, object]
    param_schema: Mapping[str, ParamField]
    # supports_* flags let the API validate BarsData before dispatching.
    supports_funding: bool = False
    supports_metrics: bool = False
    description: str = ""


# Module-level singleton. Populated at import time as strategy modules load.
_REGISTRY: dict[str, StrategyEntry] = {}


def register(entry: StrategyEntry) -> None:
    """Add ``entry`` to the registry.

    Raises ``ValueError`` on a name collision *unless* the existing entry is
    literally the same object — this makes repeated imports (common under
    pytest's module cache) idempotent rather than fatal.
    """
    existing = _REGISTRY.get(entry.name)
    if existing is not None and existing is not entry:
        raise ValueError(f"strategy {entry.name!r} is already registered")
    _REGISTRY[entry.name] = entry


def get(name: str) -> StrategyEntry:
    """Return the ``StrategyEntry`` for ``name`` or raise ``KeyError``."""
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"no strategy registered under {name!r}") from exc


def all_entries() -> list[StrategyEntry]:
    """Snapshot of every registered strategy, sorted by name."""
    return sorted(_REGISTRY.values(), key=lambda e: e.name)


def build(name: str) -> Strategy:
    """Construct a fresh ``Strategy`` instance for ``name``."""
    return get(name).factory()


__all__ = [
    "ParamField",
    "StrategyEntry",
    "all_entries",
    "build",
    "get",
    "register",
]
