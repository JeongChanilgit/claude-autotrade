"""Unit tests for kairos.backtest.quantize."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from kairos.backtest.protocols import SymbolSpec
from kairos.backtest.quantize import (
    load_symbol_specs,
    meets_min_notional,
    quantize_price,
    quantize_qty,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BTC_SPEC = SymbolSpec(
    symbol="BTCUSDT",
    tick_size=Decimal("0.1"),
    step_size=Decimal("0.001"),
    min_qty=Decimal("0.001"),
    min_notional=Decimal("5"),
)

SOL_SPEC = SymbolSpec(
    symbol="SOLUSDT",
    tick_size=Decimal("0.0010"),  # 4 decimal places exponent
    step_size=Decimal("1"),
    min_qty=Decimal("1"),
    min_notional=Decimal("5"),
)


# ---------------------------------------------------------------------------
# 1. quantize_price — buy rounds UP
# ---------------------------------------------------------------------------

def test_quantize_price_buy_rounds_up() -> None:
    result = quantize_price(70000.1234, side=1, spec=BTC_SPEC)
    assert result == Decimal("70000.2")
    assert isinstance(result, Decimal)


# ---------------------------------------------------------------------------
# 2. quantize_price — sell rounds DOWN
# ---------------------------------------------------------------------------

def test_quantize_price_sell_rounds_down() -> None:
    result = quantize_price(70000.1234, side=-1, spec=BTC_SPEC)
    assert result == Decimal("70000.1")
    assert isinstance(result, Decimal)


# ---------------------------------------------------------------------------
# 3. quantize_qty — rounds floor to step_size
# ---------------------------------------------------------------------------

def test_quantize_qty_rounds_down() -> None:
    result = quantize_qty(0.0019, spec=BTC_SPEC)
    assert result == Decimal("0.001")


# ---------------------------------------------------------------------------
# 4. quantize_qty — below min_qty returns zero
# ---------------------------------------------------------------------------

def test_quantize_qty_below_min_returns_zero() -> None:
    result = quantize_qty(0.0005, spec=BTC_SPEC)
    assert result == Decimal("0")


# ---------------------------------------------------------------------------
# 5. meets_min_notional — boundary cases
# ---------------------------------------------------------------------------

def test_meets_min_notional() -> None:
    assert meets_min_notional(Decimal("5"), Decimal("1"), SOL_SPEC) is True
    assert meets_min_notional(Decimal("4.99"), Decimal("1"), SOL_SPEC) is False


# ---------------------------------------------------------------------------
# 6. load_symbol_specs — JSON round-trip
# ---------------------------------------------------------------------------

def test_load_specs_from_json_round_trip(tmp_path: Path) -> None:
    payload = {
        "TESTUSDT": {
            "tick_size": "0.01",
            "step_size": "0.1",
            "min_qty": "0.1",
            "min_notional": "10",
        }
    }
    json_file = tmp_path / "specs.json"
    json_file.write_text(json.dumps(payload), encoding="utf-8")

    specs = load_symbol_specs(json_file)

    assert "TESTUSDT" in specs
    spec = specs["TESTUSDT"]
    assert isinstance(spec.tick_size, Decimal)
    assert isinstance(spec.step_size, Decimal)
    assert isinstance(spec.min_qty, Decimal)
    assert isinstance(spec.min_notional, Decimal)
    assert spec.tick_size == Decimal("0.01")
    assert spec.min_notional == Decimal("10")


# ---------------------------------------------------------------------------
# 7. load_symbol_specs — falls back gracefully on missing file
# ---------------------------------------------------------------------------

def test_load_specs_fallback_on_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.json"
    specs = load_symbol_specs(missing)

    # Must still return the 3 seed symbols without raising
    assert "BTCUSDT" in specs
    assert "ETHUSDT" in specs
    assert "SOLUSDT" in specs


# ---------------------------------------------------------------------------
# 8. float input via Decimal(str(x)) avoids binary noise
# ---------------------------------------------------------------------------

def test_float_input_via_decimal_str_avoids_noise() -> None:
    # Demonstrate that converting via str avoids silent binary noise.
    # Decimal(float) for 1.1 gives 1.100000000000000088817... which would
    # round UP to 1.2 when quantizing to 0.1 — wrong for a sell.
    # Decimal(str(1.1)) gives exactly Decimal('1.1'), which stays at 1.1.
    noisy_via_direct = Decimal(1.1)   # has trailing noise digits
    safe_via_str = Decimal(str(1.1))  # exact string representation

    # Both inputs feed the same float value; str-path must NOT over-round
    result_sell = quantize_price(1.1, side=-1, spec=BTC_SPEC)
    # str(1.1) == '1.1', so Decimal('1.1') quantized DOWN to tick 0.1 → 1.1
    assert result_sell == Decimal("1.1")

    # Confirm that direct Decimal(float) would have noise
    assert noisy_via_direct != safe_via_str


# ---------------------------------------------------------------------------
# 9. SOL tick_size exponent derived correctly (0.0010 → 4 decimal places)
# ---------------------------------------------------------------------------

def test_sol_tick_size_exponent_four_decimal_places() -> None:
    # tick_size '0.0010' has exponent -4; quantization must preserve 4 dp
    result = quantize_price(123.45678, side=1, spec=SOL_SPEC)
    # Rounded up to 4 dp from 123.45678 → 123.4568
    assert result == Decimal("123.4568")
    # Verify it has exactly 4 decimal places
    sign, digits, exp = result.as_tuple()
    assert exp == -4


# ---------------------------------------------------------------------------
# 10. quantize_price — exact tick boundary (no rounding needed)
# ---------------------------------------------------------------------------

def test_quantize_price_exact_tick_no_change() -> None:
    # 70000.1 is already on a tick; buy and sell both return same value
    assert quantize_price(70000.1, side=1, spec=BTC_SPEC) == Decimal("70000.1")
    assert quantize_price(70000.1, side=-1, spec=BTC_SPEC) == Decimal("70000.1")
