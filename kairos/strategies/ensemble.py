"""Weighted ensemble of A (ORB) / B (Funding Counter) / C (Vol Compression).

See docs/strategies/E-ensemble.md for the frozen spec. This module implements
the contract verbatim. Constituent strategies are injected in the constructor
for test isolation; use `build_default_ensemble()` for the production wiring.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from kairos.backtest.protocols import BarsData, SignalFrame, Strategy


class EnsembleStrategy:
    """Combine 3 sub-strategies into a single SignalFrame via weighted sum.

    Correlation-shrink: when mean pairwise rolling correlation of the sub
    strategies' `side * size_hint` streams exceeds `corr_threshold`, scale down
    the combined magnitude to `corr_shrink_min` at ρ̄ = 1.0.
    """

    name: str = "ensemble"
    default_params: Mapping[str, Any] = {
        "weights": {"orb": 0.4, "funding_counter": 0.3, "vol_compression": 0.3},
        "corr_window": 1440,
        "corr_threshold": 0.6,
        "corr_shrink_min": 0.5,
        "signal_threshold": 0.15,
    }

    def __init__(self, strategies: Mapping[str, Strategy]) -> None:
        self._strategies: dict[str, Strategy] = dict(strategies)

    def generate_signals(
        self,
        data: BarsData,
        params: Mapping[str, Any],
    ) -> SignalFrame:
        merged: dict[str, Any] = {**self.default_params, **dict(params)}
        weights: dict[str, float] = dict(merged["weights"])
        corr_window = int(merged["corr_window"])
        corr_threshold = float(merged["corr_threshold"])
        corr_shrink_min = float(merged["corr_shrink_min"])
        signal_threshold = float(merged["signal_threshold"])

        if abs(sum(weights.values()) - 1.0) > 1e-9:
            raise ValueError(f"weights must sum to 1.0, got {sum(weights.values())}")

        missing = set(weights) - set(self._strategies)
        if missing:
            raise ValueError(f"ensemble missing strategies for weights: {sorted(missing)}")

        index = data.ohlcv.index
        signed_df = pd.DataFrame(index=index, dtype=float)

        for sub_name in weights:
            sub = self._strategies[sub_name]
            sub_sf = sub.generate_signals(data, sub.default_params)
            if not sub_sf.df.index.equals(index):
                raise ValueError(
                    f"sub-strategy {sub_name!r} returned index mismatched with ohlcv"
                )
            signed_df[sub_name] = (
                sub_sf.df["side"].astype(float) * sub_sf.df["size_hint"].astype(float)
            )

        cols = list(weights.keys())
        pair_corrs: list[pd.Series] = []
        for i in range(len(cols)):
            for j in range(i + 1, len(cols)):
                c = signed_df[cols[i]].rolling(corr_window).corr(signed_df[cols[j]])
                pair_corrs.append(c)

        rho_bar = (
            pd.concat(pair_corrs, axis=1).mean(axis=1, skipna=True)
            if pair_corrs
            else pd.Series(np.nan, index=index)
        )

        excess = (rho_bar - corr_threshold).clip(lower=0)
        denom = max(1.0 - corr_threshold, 1e-12)
        raw_shrink = 1.0 - excess / denom
        shrink = raw_shrink.clip(lower=corr_shrink_min, upper=1.0).fillna(1.0)

        weighted = sum(w * signed_df[name] for name, w in weights.items()) * shrink

        side = pd.Series(0, index=index, dtype=np.int8)
        side[weighted > signal_threshold] = 1
        side[weighted < -signal_threshold] = -1

        size_hint = weighted.abs().clip(upper=1.0)
        size_hint = size_hint.where(side != 0, 0.0)

        df = pd.DataFrame(
            {
                "side": side.astype(np.int8),
                "size_hint": size_hint.astype(float),
                "stop": np.full(len(index), np.nan, dtype=float),
                "target": np.full(len(index), np.nan, dtype=float),
            },
            index=index,
        )
        return SignalFrame(df=df)


def build_default_ensemble() -> EnsembleStrategy:
    """Production wiring — ORB + Funding Counter + Vol Compression with post-D weights."""
    # Sub-strategies are fetched through the registry so the ensemble never
    # owns a direct import path to its components.
    from kairos.strategies import registry

    return EnsembleStrategy(
        {
            "orb": registry.build("orb"),
            "funding_counter": registry.build("funding_counter"),
            "vol_compression": registry.build("vol_compression"),
        }
    )


__all__ = ["EnsembleStrategy", "build_default_ensemble"]


# --- Registry self-registration -------------------------------------------
# Must be imported AFTER its sub-strategies have registered themselves, so
# the factory below can resolve them. `kairos/strategies/__init__.py`
# enforces that import order.
from kairos.strategies.registry import ParamField, StrategyEntry, register

register(
    StrategyEntry(
        name="ensemble",
        label="Ensemble (ORB + Funding + Vol-Compression)",
        factory=lambda: build_default_ensemble(),
        default_params=EnsembleStrategy.default_params,
        param_schema={
            "weights": ParamField(
                type="string", default={"orb": 0.4, "funding_counter": 0.3, "vol_compression": 0.3},
                help="Per-sub-strategy weight dict; values must sum to 1.0.",
            ),
            "corr_window": ParamField(
                type="int", default=1440, min=2, max=100000,
                help="Rolling window (bars) for pairwise correlation of sub signals.",
            ),
            "corr_threshold": ParamField(
                type="float", default=0.6, min=0.0, max=1.0,
                help="Mean pairwise correlation above which magnitude is shrunk.",
            ),
            "corr_shrink_min": ParamField(
                type="float", default=0.5, min=0.0, max=1.0,
                help="Minimum shrink factor at mean correlation = 1.0.",
            ),
            "signal_threshold": ParamField(
                type="float", default=0.15, min=0.0, max=1.0,
                help="Absolute weighted-signal threshold for a non-flat side.",
            ),
        },
        # The ensemble inherits its sub-strategies' data requirements — the
        # funding_counter sub requires both funding and metrics.
        supports_funding=True,
        supports_metrics=True,
        description=(
            "Weighted sum of ORB, Funding Counter, and Vol-Compression "
            "signals with a correlation-shrink penalty on magnitude."
        ),
    )
)
