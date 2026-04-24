# Phase 5 — Strategy Registry (FROZEN)

Replaces ad-hoc `if name == "orb": ...` chains with a single registry that
strategies self-register into. New strategies drop in a file + decorator;
the API and frontend pick them up automatically.

## Module: `kairos/strategies/registry.py`

```python
@dataclass(frozen=True, slots=True)
class StrategyEntry:
    name: str                                    # unique key, e.g. "orb"
    label: str                                   # UI display, e.g. "Opening Range Breakout"
    factory: Callable[[], Strategy]              # zero-arg constructor (params come at run time)
    default_params: Mapping[str, object]
    param_schema: Mapping[str, ParamField]       # for UI form generation
    supports_funding: bool = False               # True => requires BarsData.funding
    supports_metrics: bool = False               # True => requires BarsData.metrics
    description: str = ""


@dataclass(frozen=True, slots=True)
class ParamField:
    type: Literal["int", "float", "bool", "string", "enum"]
    default: object
    min: float | None = None
    max: float | None = None
    choices: tuple[str, ...] | None = None       # for enum
    help: str = ""


_REGISTRY: dict[str, StrategyEntry] = {}


def register(entry: StrategyEntry) -> None:
    """Add to registry; raises if name collides."""

def get(name: str) -> StrategyEntry:
    """Lookup by name; raises KeyError on miss."""

def all_entries() -> list[StrategyEntry]:
    """Snapshot of every registered strategy, sorted by name."""

def build(name: str) -> Strategy:
    """Construct a fresh Strategy instance from the registry."""
```

## Self-registration pattern

Each strategy module ends with:

```python
# kairos/strategies/orb.py
from kairos.strategies.registry import register, StrategyEntry, ParamField

register(StrategyEntry(
    name="orb",
    label="Opening Range Breakout",
    factory=lambda: ORBStrategy(),
    default_params=ORBStrategy.default_params,
    param_schema={
        "atr_period": ParamField(type="int", default=14, min=2, max=200),
        "volume_filter_mult": ParamField(type="float", default=1.5, min=0.5, max=10.0),
        # ...
    },
    supports_funding=False,
    supports_metrics=False,
    description="Trades the breakout of the first 15-minute UTC bar's range, filtered by ATR + volume.",
))
```

## Eager import

`kairos/strategies/__init__.py` imports all strategy modules so registration
fires on package import. New strategies must be added to that import list once.

## API endpoint

`GET /api/strategies` returns:
```json
{"strategies": [
  {"name": "orb", "label": "...", "default_params": {...}, "param_schema": {...},
   "supports_funding": false, "supports_metrics": false, "description": "..."},
  ...
]}
```

The frontend RunNew page calls this on mount to render the strategy dropdown
and a per-strategy params form generated from `param_schema`.

## Migration of existing strategies

- `orb.py`, `funding_counter.py`, `vol_compression.py` each gain a `register(...)` call at module bottom.
- `ensemble.py` registers as `ensemble` with composite params (sub-weights + corr params from existing default_params).
- `build_default_ensemble()` factory is **kept** but rewritten to fetch sub-strategies via `registry.build(...)` instead of direct imports.
