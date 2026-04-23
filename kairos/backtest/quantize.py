"""Decimal-based price/quantity quantization for Binance USDⓈ-M perpetuals.

Public API
----------
quantize_price   -- round price adversely (buy→up, sell→down) to tick_size
quantize_qty     -- round qty down to step_size; return 0 if below min_qty
meets_min_notional -- True iff price*qty >= spec.min_notional
load_symbol_specs   -- load {symbol: SymbolSpec} from JSON (with seed fallback)
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from typing import Literal

from kairos.backtest.protocols import SymbolSpec

_log = logging.getLogger("kairos.backtest.quantize")

# ---------------------------------------------------------------------------
# Seed data — hardcoded fallback AND source for _symbol_specs.json
# ---------------------------------------------------------------------------

_SEED: dict[str, dict[str, str]] = {
    "BTCUSDT": {
        "tick_size": "0.1",
        "step_size": "0.001",
        "min_qty": "0.001",
        "min_notional": "5",
    },
    "ETHUSDT": {
        "tick_size": "0.01",
        "step_size": "0.001",
        "min_qty": "0.001",
        "min_notional": "5",
    },
    "SOLUSDT": {
        "tick_size": "0.0010",
        "step_size": "1",
        "min_qty": "1",
        "min_notional": "5",
    },
}

_DEFAULT_JSON = Path(__file__).parent / "_symbol_specs.json"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _to_decimal(value: Decimal | float | str) -> Decimal:
    """Convert to Decimal without binary float noise."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _quantize_exponent(reference: Decimal) -> Decimal:
    """Return a Decimal whose exponent matches *reference*.

    Works correctly even when the coefficient has trailing zeros that are
    significant (e.g. Decimal('0.0010') has exponent -4, not -3).
    """
    sign, _digits, exponent = reference.as_tuple()
    # exponent is negative for fractional values (e.g. -4 for 0.0010)
    return Decimal(10) ** exponent


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def quantize_price(
    price: Decimal | float,
    side: Literal[1, -1],
    spec: SymbolSpec,
) -> Decimal:
    """Round to nearest tick_size in an ADVERSE-to-trader direction.

    Buys (side=+1) round UP (pay more).
    Sells (side=-1) round DOWN (receive less).
    Returns a Decimal quantized to the same number of decimal places as
    tick_size.
    """
    d_price = _to_decimal(price)
    quantum = _quantize_exponent(spec.tick_size)
    rounding = ROUND_CEILING if side == 1 else ROUND_FLOOR
    return d_price.quantize(quantum, rounding=rounding)


def quantize_qty(qty: Decimal | float, spec: SymbolSpec) -> Decimal:
    """Round DOWN to nearest step_size.

    If the result is < min_qty, return Decimal('0').
    """
    d_qty = _to_decimal(qty)
    quantum = _quantize_exponent(spec.step_size)
    rounded = d_qty.quantize(quantum, rounding=ROUND_FLOOR)
    if rounded < spec.min_qty:
        return Decimal("0")
    return rounded


def meets_min_notional(price: Decimal, qty: Decimal, spec: SymbolSpec) -> bool:
    """True iff price * qty >= spec.min_notional."""
    return price * qty >= spec.min_notional


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _spec_from_dict(symbol: str, row: dict[str, str]) -> SymbolSpec:
    return SymbolSpec(
        symbol=symbol,
        tick_size=Decimal(row["tick_size"]),
        step_size=Decimal(row["step_size"]),
        min_qty=Decimal(row["min_qty"]),
        min_notional=Decimal(row["min_notional"]),
    )


def _load_seed() -> dict[str, SymbolSpec]:
    return {sym: _spec_from_dict(sym, row) for sym, row in _SEED.items()}


def load_symbol_specs(path: Path | None = None) -> dict[str, SymbolSpec]:
    """Load a cached Binance exchangeInfo-derived mapping.

    If *path* is None, read from kairos/backtest/_symbol_specs.json shipped
    with the package. If the file does not exist or is corrupt, fall back to
    the hardcoded seed containing BTCUSDT, ETHUSDT, SOLUSDT.
    """
    resolved = path if path is not None else _DEFAULT_JSON
    try:
        raw = resolved.read_text(encoding="utf-8")
        data: dict[str, dict[str, str]] = json.loads(raw)
        return {sym: _spec_from_dict(sym, row) for sym, row in data.items()}
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "Could not load symbol specs from %s (%s); using hardcoded seed.",
            resolved,
            exc,
        )
        return _load_seed()


__all__ = [
    "load_symbol_specs",
    "meets_min_notional",
    "quantize_price",
    "quantize_qty",
]
