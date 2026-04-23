"""Tests for regime window registry."""
from __future__ import annotations

import pandas as pd
import pytest

from kairos.backtest.regimes import (
    REGIMES,
    RegimeWindow,
    get_regime,
    get_window,
)


def test_all_regime_names_covered() -> None:
    """All 4 regime families must have at least one window."""
    names = {w.name for w in REGIMES}
    assert names == {"uptrend", "range", "crash", "lowvol"}


def test_windows_tz_aware_and_ordered() -> None:
    for w in REGIMES:
        assert w.start.tzinfo is not None
        assert w.end.tzinfo is not None
        assert w.start < w.end


def test_labels_are_unique() -> None:
    labels = [w.label for w in REGIMES]
    assert len(labels) == len(set(labels)), f"duplicate labels in REGIMES: {labels}"


def test_crash_windows_are_short() -> None:
    """Crash windows should be <= 14 days — they're stress tests, not sustained regimes."""
    for w in get_regime("crash"):
        assert (w.end - w.start) <= pd.Timedelta(days=14), w.label


def test_get_regime_returns_subset() -> None:
    crashes = get_regime("crash")
    assert len(crashes) >= 1
    assert all(w.name == "crash" for w in crashes)


def test_get_window_by_label() -> None:
    w = get_window("luna_collapse")
    assert w.name == "crash"
    assert w.start.year == 2022 and w.start.month == 5


def test_get_window_missing_raises() -> None:
    with pytest.raises(KeyError):
        get_window("does_not_exist")


def test_regime_window_rejects_naive() -> None:
    with pytest.raises(ValueError, match="UTC-aware"):
        RegimeWindow("uptrend", "x", pd.Timestamp("2024-01-01"), pd.Timestamp("2024-02-01", tz="UTC"))


def test_regime_window_rejects_inverted() -> None:
    with pytest.raises(ValueError, match="strictly before"):
        RegimeWindow(
            "uptrend", "x",
            pd.Timestamp("2024-02-01", tz="UTC"),
            pd.Timestamp("2024-01-01", tz="UTC"),
        )
