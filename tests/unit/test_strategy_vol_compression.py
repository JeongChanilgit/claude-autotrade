"""Golden-case tests for Strategy C — Volatility Compression Breakout.

Per the frozen spec at ``docs/strategies/C-vol-compression.md`` §Golden cases.

Construction notes
------------------
All five golden cases share a common base OHLCV skeleton constructed around
a constant close=100 with:

* A warmup "zig-zag" on bars 1..13 (close = 100 ± 0.1) to force both
  avg_gain and avg_loss to be strictly positive, so Wilder RSI is defined
  from bar 14 onward (otherwise a fully flat warmup leaves RSI = NaN).
* A flat region on bars 14..33 that lets avg_gain/avg_loss decay toward
  equality, driving RSI toward ~38.7 (the asymptote implied by the
  zig-zag's residual Wilder-smoothed averages — it is not 50.0 because
  the zig-zag was not perfectly symmetric in the smoothing window).
* Intrabar range ``high - low = 4`` on every bar, which pins per-bar
  Wilder ATR close to 4. With ``kc_mult=1.5``, KC half-width ≈ 6, which
  comfortably contains the BB half-width (≈ 2*σ_close < 6) throughout
  the flat region, so the squeeze condition is True on bars 30..50.
* A large close jump at bar 51 (e.g. to 115) which inflates the 20-bar
  rolling σ enough to push BB outside KC, thus creating the squeeze
  *release* precisely at bar 51.

Divergence construction for GC-C1 (bullish):
    * bar 34: close = 99.0 — a minor dip, Wilder-RSI(14) = 12.749
      (avg_loss just absorbed the first true loss while avg_gain is
      still near-zero from the warmup).
    * bar 40: close = 98.5 — a larger dip in absolute price, but the
      avg_loss has decayed between bars 35..39, so Wilder-RSI(14) at
      bar 40 is 25.814, i.e. HIGHER than the RSI at bar 34 (12.749).
    * Within the divergence_lookback=20 window ending at bar 51
      (bars 32..51), the argmin of close is bar 40 (98.5) but the
      argmin of RSI is bar 34 (12.749). argmin_price != argmin_rsi →
      bullish divergence.

GC-C3 (bearish) mirrors this: bar 34 close=101.0 and bar 40 close=101.5.
Because the first uptick hits a fresh avg_gain while avg_loss is low, the
RSI at bar 34 is 79.776, and by bar 40 it has decayed to 71.914 despite
the larger price move. argmax_price (bar 40) != argmax_rsi (bar 34).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kairos.backtest.protocols import BarsData, SignalFrame
from kairos.strategies.vol_compression import (
    VolCompressionStrategy,
    _wilder_atr,
    _wilder_rsi,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(close: np.ndarray, spread: float = 2.0) -> pd.DataFrame:
    """Wrap a 1-D close array into a full OHLCV DataFrame with a UTC index.

    open == close (irrelevant to the strategy), and high/low are symmetric
    around close by ``spread`` so every bar has a ``high - low`` range of
    ``2 * spread``.
    """
    n = len(close)
    idx = pd.date_range("2024-01-01", periods=n, freq="1min", tz="UTC")
    high = close + spread
    low = close - spread
    return pd.DataFrame(
        {
            "open": close.astype(np.float64),
            "high": high.astype(np.float64),
            "low": low.astype(np.float64),
            "close": close.astype(np.float64),
            "volume": np.ones(n, dtype=np.float64),
            "quote_volume": np.ones(n, dtype=np.float64),
            "taker_buy_volume": np.ones(n, dtype=np.float64),
            "taker_buy_quote_volume": np.ones(n, dtype=np.float64),
            "trade_count": np.ones(n, dtype=np.int64),
        },
        index=idx,
    )


def _base_close(n: int) -> np.ndarray:
    """Warmup zigzag on 1..13, then flat 100.0."""
    close = np.full(n, 100.0, dtype=np.float64)
    for i in range(1, 14):
        close[i] = 100.0 + (0.1 if i % 2 == 0 else -0.1)
    # bars 14..n-1 start flat; callers overlay the case-specific moves.
    close[14:] = 100.0
    return close


def _bullish_close(n: int) -> np.ndarray:
    """Base + bar-34 mini-dip + bar-40 deeper-but-later dip + bar-51 breakout.

    The critical invariant is ``argmin(close[32:52]) = 40`` while
    ``argmin(RSI[32:52]) = 34`` — see module docstring for the numeric
    reasoning.
    """
    close = _base_close(n)
    close[34] = 99.0
    close[35] = 100.0
    close[40] = 98.5
    close[41] = 100.0
    close[51] = 115.0
    # Post-breakout default: flat at 100 (callers may override).
    close[52:] = 100.0
    return close


def _bearish_close(n: int) -> np.ndarray:
    """Mirror of ``_bullish_close`` for bearish divergence."""
    close = _base_close(n)
    close[34] = 101.0
    close[35] = 100.0
    close[40] = 101.5
    close[41] = 100.0
    close[51] = 85.0  # large drop widens BB past KC and crosses mid down
    close[52:] = 100.0
    return close


def _run(close: np.ndarray, params: dict | None = None) -> SignalFrame:
    data = BarsData(ohlcv=_make_ohlcv(close), symbol="TESTUSDT", interval="1m")
    return VolCompressionStrategy().generate_signals(data, params or {})


# ---------------------------------------------------------------------------
# Frame contract
# ---------------------------------------------------------------------------

def _assert_frame_contract(sf: SignalFrame, ohlcv_index: pd.DatetimeIndex) -> None:
    df = sf.df
    assert list(df.columns) == ["side", "size_hint", "stop", "target"]
    assert df["side"].dtype == np.int8
    assert df["size_hint"].dtype == np.float64
    assert df["stop"].dtype == np.float64
    assert df["target"].dtype == np.float64
    assert df.index.equals(ohlcv_index)
    # size_hint always 1.0
    assert (df["size_hint"] == 1.0).all()
    # stop/target always NaN
    assert df["stop"].isna().all()
    assert df["target"].isna().all()


# ---------------------------------------------------------------------------
# GC-C1: squeeze release + bullish divergence → long entry at bar 51
# ---------------------------------------------------------------------------

def test_gc_c1_bullish_entry_on_squeeze_release() -> None:
    n = 300
    close = _bullish_close(n)
    sf = _run(close)
    df = sf.df
    _assert_frame_contract(sf, _make_ohlcv(close).index)

    # --- Verify the divergence construction explicitly --------------------
    # RSI at bar 34 is 12.749 and at bar 40 is 25.814. The argmin over the
    # 20-bar window [32..51] is therefore bar 34 (RSI) vs bar 40 (close).
    rsi = _wilder_rsi(pd.Series(close), 14)
    window_close = close[32:52]
    window_rsi = rsi.iloc[32:52].to_numpy()
    assert int(np.argmin(window_close)) == (40 - 32)
    assert int(np.argmin(window_rsi)) == (34 - 32)
    # Sanity: the price-min bar's RSI must exceed the window-min RSI.
    assert window_rsi[40 - 32] > window_rsi[34 - 32]

    # --- Verify squeeze release at exactly bar 51 -------------------------
    c = pd.Series(close); h = pd.Series(close + 2.0); l = pd.Series(close - 2.0)
    mid = c.rolling(20).mean()
    std = c.rolling(20).std(ddof=0)
    ubb, lbb = mid + 2.0 * std, mid - 2.0 * std
    ema = c.ewm(span=20, adjust=False).mean()
    atr = _wilder_atr(h, l, c, 20)
    ukc, lkc = ema + 1.5 * atr, ema - 1.5 * atr
    squeeze = (ubb < ukc) & (lbb > lkc)
    assert squeeze.iloc[50] and not squeeze.iloc[51]
    # The release bar's close must be above mid for a long entry.
    assert close[51] > mid.iloc[51]

    # --- Signal expectation ----------------------------------------------
    assert df["side"].iloc[51] == 1
    assert (df["side"].iloc[:51] == 0).all()


# ---------------------------------------------------------------------------
# GC-C2: squeeze release without divergence → no signal
# ---------------------------------------------------------------------------

def test_gc_c2_no_divergence_no_signal() -> None:
    """Remove the bar-34 RSI "pit" so RSI's argmin coincides with the price
    argmin at bar 40 — no divergence. Squeeze release at bar 51 should
    produce no entry."""
    n = 300
    close = _base_close(n)
    # Only one dip, at bar 40, with recovery. No earlier RSI pit.
    close[40] = 98.5
    close[41] = 100.0
    close[51] = 115.0  # still release the squeeze + cross mid
    close[52:] = 100.0

    sf = _run(close)
    df = sf.df
    _assert_frame_contract(sf, _make_ohlcv(close).index)

    # Sanity: there IS a squeeze release at bar 51 despite the 0 signal.
    c = pd.Series(close); h = pd.Series(close + 2.0); l = pd.Series(close - 2.0)
    mid = c.rolling(20).mean()
    std = c.rolling(20).std(ddof=0)
    ubb, lbb = mid + 2.0 * std, mid - 2.0 * std
    ema = c.ewm(span=20, adjust=False).mean()
    atr = _wilder_atr(h, l, c, 20)
    ukc, lkc = ema + 1.5 * atr, ema - 1.5 * atr
    squeeze = (ubb < ukc) & (lbb > lkc)
    assert squeeze.iloc[50] and not squeeze.iloc[51]

    # And argmin(close) == argmin(rsi) in the window → no divergence.
    rsi = _wilder_rsi(c, 14)
    window_close = close[32:52]
    window_rsi = rsi.iloc[32:52].to_numpy()
    assert int(np.argmin(window_close)) == int(np.argmin(window_rsi))

    assert (df["side"] == 0).all()


# ---------------------------------------------------------------------------
# GC-C3: squeeze release + bearish divergence → short at bar 51
# ---------------------------------------------------------------------------

def test_gc_c3_bearish_entry_on_squeeze_release() -> None:
    n = 300
    close = _bearish_close(n)
    sf = _run(close)
    df = sf.df
    _assert_frame_contract(sf, _make_ohlcv(close).index)

    # RSI at bar 34 is 79.776 and at bar 40 is 71.914 — the earlier,
    # smaller uptick hits a freshly-initialized avg_loss and so spikes
    # RSI HIGHER than the later, larger uptick (by which time avg_gain
    # has decayed closer to avg_loss).
    rsi = _wilder_rsi(pd.Series(close), 14)
    window_close = close[32:52]
    window_rsi = rsi.iloc[32:52].to_numpy()
    assert int(np.argmax(window_close)) == (40 - 32)
    assert int(np.argmax(window_rsi)) == (34 - 32)
    assert window_rsi[40 - 32] < window_rsi[34 - 32]

    assert df["side"].iloc[51] == -1
    assert (df["side"].iloc[:51] == 0).all()


# ---------------------------------------------------------------------------
# GC-C4: opposite mid-cross flatten after bullish entry
# ---------------------------------------------------------------------------

def test_gc_c4_mid_cross_flatten() -> None:
    """GC-C1 setup, then hold close at 115 so the position survives (close
    remains >= mid), then drop to 95 at bar 80 which crosses mid downward
    for a long → flatten."""
    n = 300
    close = _bullish_close(n)
    # Keep close elevated so the long position is not flattened at bar 52.
    close[52:80] = 115.0
    close[80] = 95.0
    close[81:] = 115.0

    sf = _run(close)
    df = sf.df
    _assert_frame_contract(sf, _make_ohlcv(close).index)

    # Entry fires at bar 51 and the position is held through bar 79.
    assert df["side"].iloc[51] == 1
    assert (df["side"].iloc[51:80] == 1).all()
    # Bar 80's close (95) is below the 20-bar rolling mid → flatten.
    mid80 = pd.Series(close).rolling(20).mean().iloc[80]
    assert close[80] < mid80
    assert df["side"].iloc[80] == 0


# ---------------------------------------------------------------------------
# GC-C5: holding_bars timeout flatten (default holding_bars=240)
# ---------------------------------------------------------------------------

def test_gc_c5_holding_bars_timeout() -> None:
    """Entry at bar 51, close stays at 115 indefinitely (never crosses mid
    downward), so only the holding_bars=240 timer can flatten.

    With entry_bar=51 and holding_bars=240, at bar t=51+240=291 the check
    ``t - entry_bar >= holding_bars`` triggers flatten → side[291]=0.
    That is the "241st bar of holding" per the spec (bar 51 itself is the
    1st bar held, bar 291 is the 241st).
    """
    n = 400
    close = _bullish_close(n)
    close[51:] = 115.0  # stay high forever

    sf = _run(close)
    df = sf.df
    _assert_frame_contract(sf, _make_ohlcv(close).index)

    assert df["side"].iloc[51] == 1
    # Held through bar 290 (239 bars after entry).
    assert (df["side"].iloc[51:291] == 1).all()
    # Flattened at bar 291 and thereafter.
    assert (df["side"].iloc[291:] == 0).all()


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_idempotent_same_inputs_same_outputs() -> None:
    n = 300
    close = _bullish_close(n)
    sf1 = _run(close)
    sf2 = _run(close)
    pd.testing.assert_frame_equal(sf1.df, sf2.df)
    # Dtypes also equal (not just values).
    assert (sf1.df.dtypes == sf2.df.dtypes).all()
