"""Strategy A — Opening Range Breakout (ORB).

FROZEN implementation conforming to ``docs/strategies/A-orb.md``. Deterministic,
no look-ahead, no risk/sizing logic (``size_hint`` is always 1.0).

At bar T the strategy is allowed to read bars whose ``close_time <= T`` only.
``.shift(1)`` / explicit prior-day slicing is used to enforce that invariant.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

import numpy as np
import pandas as pd

from kairos.backtest.costs import daily_atr_per_close
from kairos.backtest.protocols import BarsData, SignalFrame

_DEFAULT_PARAMS: Final[Mapping[str, Any]] = {
    "or_minutes": 15,
    "session_start_utc": "00:00",
    "session_end_utc": "23:55",
    "volume_multiple": 1.5,
    "volume_lookback_days": 20,
    "atr_min_pct": 0.01,
    "tick_buffer": 0.0,
    "orr_target_mult": 2.0,
}


def _parse_hhmm(value: str) -> tuple[int, int]:
    hh, mm = value.split(":")
    return int(hh), int(mm)


class OrbStrategy:
    """Opening Range Breakout (first ``or_minutes`` of each UTC day).

    Long: first bar after the OR window where ``high > OR_high + tick_buffer``.
    Short: first bar after the OR window where ``low < OR_low - tick_buffer``.
    Only one trade per UTC day; stop = opposite OR edge,
    target = entry ± ``orr_target_mult * OR_range``.

    Position is held (side stays +/-1) until stop/target touched within a bar
    or until ``session_end_utc`` is reached (same-bar flatten on that minute).
    """

    name: str = "orb"
    default_params: Mapping[str, Any] = _DEFAULT_PARAMS

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _merged_params(params: Mapping[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = dict(_DEFAULT_PARAMS)
        merged.update(params)
        return merged

    @staticmethod
    def _first_15min_volume_by_day(
        ohlcv: pd.DataFrame, or_minutes: int
    ) -> pd.Series:
        """Sum of ``volume`` over each UTC day's first ``or_minutes`` minutes.

        Indexed by the UTC date (tz-naive ``pd.Timestamp`` at 00:00 of that day
        but returned with a ``DatetimeIndex`` normalised to midnight UTC).
        """
        idx = ohlcv.index
        minute_of_day = idx.hour * 60 + idx.minute
        mask = minute_of_day < or_minutes
        or_bars = ohlcv.loc[mask, ["volume"]].copy()
        # Group by UTC date
        grouped = or_bars.groupby(or_bars.index.normalize())["volume"].sum()
        return grouped

    @staticmethod
    def _or_by_day(ohlcv: pd.DataFrame, or_minutes: int) -> pd.DataFrame:
        """Return a DataFrame indexed by UTC day with ``or_high`` / ``or_low``."""
        idx = ohlcv.index
        minute_of_day = idx.hour * 60 + idx.minute
        mask = minute_of_day < or_minutes
        or_bars = ohlcv.loc[mask, ["high", "low"]].copy()
        days = or_bars.index.normalize()
        out = pd.DataFrame(
            {
                "or_high": or_bars["high"].groupby(days).max(),
                "or_low": or_bars["low"].groupby(days).min(),
            }
        )
        return out

    # ------------------------------------------------------------------- filters

    def _volume_filter_pass(
        self,
        ohlcv: pd.DataFrame,
        or_minutes: int,
        lookback_days: int,
        multiple: float,
    ) -> pd.Series:
        """Boolean series indexed by UTC day: True if today's first-``or_minutes``
        volume is >= ``multiple`` × mean of the prior ``lookback_days`` days'.
        """
        vol_by_day = self._first_15min_volume_by_day(ohlcv, or_minutes)
        # Use only PRIOR N days — shift(1) then rolling.
        baseline = vol_by_day.shift(1).rolling(
            window=lookback_days, min_periods=lookback_days
        ).mean()
        pass_mask = vol_by_day >= multiple * baseline
        # Days without enough prior history -> NaN -> False (fail filter).
        return pass_mask.fillna(False)

    @staticmethod
    def _atr_filter_pass(
        ohlcv: pd.DataFrame, atr_min_pct: float
    ) -> pd.Series:
        """Boolean series indexed by UTC day: True if the ATR14/close ratio
        *entering* that day (i.e. prior day's value) is >= ``atr_min_pct``.

        Uses ``daily_atr_per_close`` on the full OHLCV, sampled per day, then
        shifted by one day to avoid look-ahead.
        """
        ratio_minute = daily_atr_per_close(ohlcv, period=14)
        # One value per UTC day (the value is forward-filled so any bar works).
        # Taking .first() per day gives us the value that was live at 00:00.
        ratio_daily = ratio_minute.groupby(ratio_minute.index.normalize()).first()
        prior_ratio = ratio_daily.shift(1)
        pass_mask = prior_ratio >= atr_min_pct
        return pass_mask.fillna(False)

    # -------------------------------------------------------------- main entry

    def generate_signals(
        self, data: BarsData, params: Mapping[str, Any]
    ) -> SignalFrame:
        p = self._merged_params(params)
        or_minutes: int = int(p["or_minutes"])
        session_end_utc: str = str(p["session_end_utc"])
        volume_multiple: float = float(p["volume_multiple"])
        volume_lookback_days: int = int(p["volume_lookback_days"])
        atr_min_pct: float = float(p["atr_min_pct"])
        tick_buffer: float = float(p["tick_buffer"])
        orr_target_mult: float = float(p["orr_target_mult"])
        end_hh, end_mm = _parse_hhmm(session_end_utc)
        end_minute_of_day = end_hh * 60 + end_mm

        ohlcv = data.ohlcv
        n = len(ohlcv)
        idx = ohlcv.index

        side = np.zeros(n, dtype=np.int8)
        size_hint = np.zeros(n, dtype=np.float64)
        stop = np.full(n, np.nan, dtype=np.float64)
        target = np.full(n, np.nan, dtype=np.float64)

        if n == 0:
            df = pd.DataFrame(
                {
                    "side": pd.Series(side, index=idx, dtype="int8"),
                    "size_hint": pd.Series(size_hint, index=idx, dtype="float64"),
                    "stop": pd.Series(stop, index=idx, dtype="float64"),
                    "target": pd.Series(target, index=idx, dtype="float64"),
                }
            )
            return SignalFrame(df=df)

        # --- Precompute per-day structures ------------------------------------
        or_by_day = self._or_by_day(ohlcv, or_minutes)
        vol_pass_by_day = self._volume_filter_pass(
            ohlcv, or_minutes, volume_lookback_days, volume_multiple
        )
        atr_pass_by_day = self._atr_filter_pass(ohlcv, atr_min_pct)

        # Group bar positions by UTC day for iteration.
        day_keys = idx.normalize()
        # Use a stable order — iterate by unique day in index order.
        unique_days = pd.DatetimeIndex(day_keys).unique()

        minute_of_day_arr = (idx.hour * 60 + idx.minute).to_numpy()
        high_arr = ohlcv["high"].to_numpy()
        low_arr = ohlcv["low"].to_numpy()
        # Pre-build positions-per-day via a pandas group lookup to avoid numpy
        # tz-aware comparison pitfalls.
        positions_by_day: dict[pd.Timestamp, np.ndarray] = {
            day: np.asarray(positions, dtype=np.int64)
            for day, positions in pd.Series(np.arange(n, dtype=np.int64), index=day_keys)
            .groupby(day_keys)
            .apply(list)
            .items()
        }

        for day in unique_days:
            day_positions = positions_by_day.get(day)
            if day_positions is None or day_positions.size == 0:
                continue

            # Filters
            if day not in or_by_day.index:
                continue
            if not bool(vol_pass_by_day.get(day, False)):
                continue
            if not bool(atr_pass_by_day.get(day, False)):
                continue

            or_high = float(or_by_day.loc[day, "or_high"])
            or_low = float(or_by_day.loc[day, "or_low"])
            or_range = or_high - or_low
            if not np.isfinite(or_range) or or_range <= 0.0:
                continue

            # Iterate the day's bars in order and find the first breakout
            # *after* the OR window completes (minute_of_day >= or_minutes).
            entry_pos: int | None = None
            entry_side: int = 0
            entry_stop: float = np.nan
            entry_target: float = np.nan

            long_thresh = or_high + tick_buffer
            short_thresh = or_low - tick_buffer

            for pos in day_positions:
                mod = minute_of_day_arr[pos]
                if mod < or_minutes:
                    continue
                h = high_arr[pos]
                lo = low_arr[pos]
                # Evaluate long and short breakouts. The spec says the *first*
                # breakout wins; if a bar triggers both (long and short), treat
                # it as ambiguous and skip (this cannot happen with OR_range>0
                # unless the bar's range straddles both edges, which is not in
                # any golden case — but guard anyway).
                long_break = h > long_thresh
                short_break = lo < short_thresh
                if long_break and not short_break:
                    entry_pos = pos
                    entry_side = 1
                    entry_stop = or_low
                    entry_target = or_high + orr_target_mult * or_range
                    break
                if short_break and not long_break:
                    entry_pos = pos
                    entry_side = -1
                    entry_stop = or_high
                    entry_target = or_low - orr_target_mult * or_range
                    break
                if long_break and short_break:
                    # Ambiguous; do nothing (should not hit golden cases).
                    continue

            if entry_pos is None:
                continue

            # Hold position from entry bar onward until stop/target hit or
            # session_end_utc. On the session_end bar itself, side is 0 (flat).
            # If stop/target hit: exit at that bar (side stays ±1 for that bar
            # per protocol — the engine exits intra-bar — and next bar is 0).
            exit_pos: int | None = None
            end_pos: int | None = None
            for pos in day_positions:
                if pos < entry_pos:
                    continue
                mod = minute_of_day_arr[pos]
                if mod >= end_minute_of_day:
                    end_pos = pos
                    break

            # Scan bars after (and including) entry for stop/target.
            hold_end = end_pos if end_pos is not None else (day_positions[-1] + 1)
            for pos in range(entry_pos, hold_end):
                h = high_arr[pos]
                lo = low_arr[pos]
                if entry_side == 1:
                    if lo <= entry_stop or h >= entry_target:
                        exit_pos = pos
                        break
                else:
                    if h >= entry_stop or lo <= entry_target:
                        exit_pos = pos
                        break

            # Determine the last bar that carries the side (inclusive). Per the
            # golden cases the Strategy itself pre-flattens: the stop-hit bar
            # and the session_end bar both receive side=0. The engine's
            # intra-bar stop/target touch handling is a separate concern and
            # operates off the persisted ``stop``/``target`` columns up to the
            # previous bar.
            if exit_pos is not None:
                last_held = exit_pos - 1  # GC-A4: stop-hit bar carries side=0
            elif end_pos is not None:
                last_held = end_pos - 1  # GC-A5: 23:55 bar carries side=0
            else:
                last_held = day_positions[-1]

            if last_held < entry_pos:
                # Stop hit on entry bar itself, or entry == session_end bar.
                continue

            # Write the side and stop/target for the held window.
            side[entry_pos : last_held + 1] = entry_side
            size_hint[entry_pos : last_held + 1] = 1.0
            stop[entry_pos : last_held + 1] = entry_stop
            target[entry_pos : last_held + 1] = entry_target

        df = pd.DataFrame(
            {
                "side": pd.Series(side, index=idx, dtype="int8"),
                "size_hint": pd.Series(size_hint, index=idx, dtype="float64"),
                "stop": pd.Series(stop, index=idx, dtype="float64"),
                "target": pd.Series(target, index=idx, dtype="float64"),
            }
        )
        return SignalFrame(df=df)


__all__ = ["OrbStrategy"]


# --- Registry self-registration -------------------------------------------
# Imported last so the module's public symbols are defined before the
# registry captures a factory referencing them.
from kairos.strategies.registry import ParamField, StrategyEntry, register

register(
    StrategyEntry(
        name="orb",
        label="Opening Range Breakout",
        factory=lambda: OrbStrategy(),
        default_params=OrbStrategy.default_params,
        param_schema={
            "or_minutes": ParamField(
                type="int", default=15, min=1, max=240,
                help="Length of the opening range window in minutes.",
            ),
            "session_start_utc": ParamField(
                type="string", default="00:00",
                help="Session start HH:MM (UTC).",
            ),
            "session_end_utc": ParamField(
                type="string", default="23:55",
                help="Session end HH:MM (UTC); positions flatten at this bar.",
            ),
            "volume_multiple": ParamField(
                type="float", default=1.5, min=0.1, max=20.0,
                help="OR volume must be >= this multiple of the prior baseline.",
            ),
            "volume_lookback_days": ParamField(
                type="int", default=20, min=1, max=200,
                help="Days of prior OR-window volume used for the baseline.",
            ),
            "atr_min_pct": ParamField(
                type="float", default=0.01, min=0.0, max=1.0,
                help="Minimum prior-day ATR14/close ratio to allow entry.",
            ),
            "tick_buffer": ParamField(
                type="float", default=0.0, min=0.0, max=1000.0,
                help="Absolute price buffer added above/below the OR edge.",
            ),
            "orr_target_mult": ParamField(
                type="float", default=2.0, min=0.1, max=20.0,
                help="Target distance in multiples of the OR range.",
            ),
        },
        supports_funding=False,
        supports_metrics=False,
        description=(
            "Trades the breakout of the first OR-minute UTC window, "
            "filtered by ATR and OR-window volume vs. a rolling baseline."
        ),
    )
)

