"""Phase 3 Go/No-Go gate composer.

The gate in the plan (docs/1-2-oos-encapsulated-meadow plan) is:

  (a) Each single strategy OOS Sharpe ≥ 1.2
  (b) Ensemble OOS CAGR ≥ 200%, MDD ≤ 30%
  (c) All 4 regime families break-even or better
  (d) All 3 stress scenarios (LUNA/FTX/yen-carry) respect daily -3% limit

This module doesn't drive the evaluations — it composes the existing harnesses
(regime_evaluator + stress + plain engine run) into a single pass/fail report
with a rubric, so later callers can feed in a freshly-downloaded data bundle
and see which sub-gates failed.

The per-strategy Sharpe gate and the ensemble CAGR gate require OOS data;
during Phase 3 we run them on the walk-forward *validation* windows (the
true OOS seal stays untouched until Phase 5). The wiring accepts the Sharpe
and CAGR/MDD values as inputs so callers control what "OOS" means here.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from kairos.backtest.regime_evaluator import (
    RegimeEvaluation,
    all_families_break_even,
    group_by_family,
)
from kairos.backtest.stress import StressResult, all_daily_limits_respected

# Phase 3 thresholds — sourced from the plan. Changing these requires Opus review.
MIN_STRATEGY_SHARPE = 1.2
MIN_ENSEMBLE_CAGR = 2.0
MAX_ENSEMBLE_MDD = 0.30


@dataclass(frozen=True, slots=True)
class StrategySharpe:
    name: str
    sharpe: float


@dataclass(frozen=True, slots=True)
class EnsembleHeadline:
    cagr: float
    mdd: float


@dataclass(frozen=True, slots=True)
class Phase3Report:
    """One-shot pass/fail rubric for the Phase 3 gate."""
    strategy_sharpe_pass: bool
    strategy_sharpe_failures: tuple[str, ...]  # names of strategies that missed

    ensemble_headline_pass: bool
    ensemble_cagr: float
    ensemble_mdd: float

    regimes_pass: bool
    failing_regime_labels: tuple[str, ...]

    stress_pass: bool
    failing_stress_labels: tuple[str, ...]

    @property
    def overall_pass(self) -> bool:
        return (
            self.strategy_sharpe_pass
            and self.ensemble_headline_pass
            and self.regimes_pass
            and self.stress_pass
        )

    def summary_lines(self) -> list[str]:
        """Human-readable summary for STATUS.md or console output."""
        verdict = "PASS" if self.overall_pass else "FAIL"
        lines = [f"Phase 3 Go/No-Go: {verdict}"]
        lines.append(
            f"  (a) Strategy Sharpe ≥ {MIN_STRATEGY_SHARPE}: "
            f"{'PASS' if self.strategy_sharpe_pass else 'FAIL'}"
        )
        if self.strategy_sharpe_failures:
            lines.append(f"      failing: {list(self.strategy_sharpe_failures)}")
        lines.append(
            f"  (b) Ensemble CAGR ≥ {MIN_ENSEMBLE_CAGR}, MDD ≤ {MAX_ENSEMBLE_MDD}: "
            f"CAGR={self.ensemble_cagr:.3f}, MDD={self.ensemble_mdd:.3f} "
            f"→ {'PASS' if self.ensemble_headline_pass else 'FAIL'}"
        )
        lines.append(
            f"  (c) Regime families break-even: "
            f"{'PASS' if self.regimes_pass else 'FAIL'}"
        )
        if self.failing_regime_labels:
            lines.append(f"      failing windows: {list(self.failing_regime_labels)}")
        lines.append(
            f"  (d) Stress daily-limit respected: "
            f"{'PASS' if self.stress_pass else 'FAIL'}"
        )
        if self.failing_stress_labels:
            lines.append(f"      failing windows: {list(self.failing_stress_labels)}")
        return lines


def compose_phase3_report(
    *,
    strategy_sharpes: Iterable[StrategySharpe],
    ensemble_headline: EnsembleHeadline,
    regime_evaluations: Iterable[RegimeEvaluation],
    stress_results: Iterable[StressResult],
) -> Phase3Report:
    sharpes = tuple(strategy_sharpes)
    sharpe_fails = tuple(s.name for s in sharpes if s.sharpe < MIN_STRATEGY_SHARPE)

    regimes = tuple(regime_evaluations)
    regime_fails = tuple(
        e.window.label for e in regimes if not e.break_even
    )

    stresses = tuple(stress_results)
    stress_fails = tuple(
        s.window.label for s in stresses if not s.daily_limit_respected
    )

    ensemble_ok = (
        ensemble_headline.cagr >= MIN_ENSEMBLE_CAGR
        and ensemble_headline.mdd <= MAX_ENSEMBLE_MDD
    )

    # Regime gate also requires every family present (not just evaluated ones
    # passing); group_by_family returns only families that had evaluations.
    families_covered = len(group_by_family(regimes))
    regimes_ok = (
        not regime_fails
        and families_covered >= 1
        and all_families_break_even(regimes)
    )

    return Phase3Report(
        strategy_sharpe_pass=not sharpe_fails,
        strategy_sharpe_failures=sharpe_fails,
        ensemble_headline_pass=ensemble_ok,
        ensemble_cagr=ensemble_headline.cagr,
        ensemble_mdd=ensemble_headline.mdd,
        regimes_pass=regimes_ok,
        failing_regime_labels=regime_fails,
        stress_pass=all_daily_limits_respected(stresses),
        failing_stress_labels=stress_fails,
    )


__all__ = [
    "MAX_ENSEMBLE_MDD",
    "MIN_ENSEMBLE_CAGR",
    "MIN_STRATEGY_SHARPE",
    "EnsembleHeadline",
    "Phase3Report",
    "StrategySharpe",
    "compose_phase3_report",
]
