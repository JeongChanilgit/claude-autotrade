"""Tests for the Phase 3 Go/No-Go gate composer."""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd

from kairos.backtest.phase3_gate import (
    EnsembleHeadline,
    StrategySharpe,
    compose_phase3_report,
)
from kairos.backtest.regime_evaluator import RegimeEvaluation
from kairos.backtest.regimes import RegimeWindow
from kairos.backtest.stress import StressResult


def _eval(label: str, family: str, total_return: float) -> RegimeEvaluation:
    w = RegimeWindow(
        name=family,  # type: ignore[arg-type]
        label=label,
        start=pd.Timestamp("2024-01-01", tz="UTC"),
        end=pd.Timestamp("2024-01-10", tz="UTC"),
    )
    return RegimeEvaluation(
        window=w,
        result=MagicMock(),
        bars=240,
        total_return=total_return,
        sharpe=1.5,
        mdd=-0.1,
    )


def _stress(label: str, respected: bool) -> StressResult:
    w = RegimeWindow(
        name="crash",
        label=label,
        start=pd.Timestamp("2024-01-01", tz="UTC"),
        end=pd.Timestamp("2024-01-10", tz="UTC"),
    )
    return StressResult(
        window=w,
        raw_result=MagicMock(),
        protected_result=MagicMock(),
        raw_halts=(),
        worst_daily_return=-0.02 if respected else -0.06,
        daily_limit_respected=respected,
    )


def test_all_gates_pass() -> None:
    rep = compose_phase3_report(
        strategy_sharpes=[
            StrategySharpe("orb", 1.3),
            StrategySharpe("funding_counter", 1.5),
            StrategySharpe("vol_compression", 1.21),
        ],
        ensemble_headline=EnsembleHeadline(cagr=2.5, mdd=0.25),
        regime_evaluations=[
            _eval("bull", "uptrend", 0.2),
            _eval("range", "range", 0.05),
            _eval("luna", "crash", 0.01),
            _eval("lowvol", "lowvol", 0.08),
        ],
        stress_results=[_stress("luna", True), _stress("ftx", True), _stress("yen", True)],
    )
    assert rep.overall_pass is True
    assert rep.strategy_sharpe_pass
    assert rep.ensemble_headline_pass
    assert rep.regimes_pass
    assert rep.stress_pass


def test_strategy_sharpe_failure_reports_names() -> None:
    rep = compose_phase3_report(
        strategy_sharpes=[
            StrategySharpe("orb", 1.1),  # below 1.2
            StrategySharpe("funding_counter", 1.5),
        ],
        ensemble_headline=EnsembleHeadline(cagr=2.5, mdd=0.25),
        regime_evaluations=[_eval("bull", "uptrend", 0.1)],
        stress_results=[_stress("luna", True)],
    )
    assert rep.strategy_sharpe_pass is False
    assert rep.strategy_sharpe_failures == ("orb",)
    assert rep.overall_pass is False


def test_ensemble_cagr_failure() -> None:
    rep = compose_phase3_report(
        strategy_sharpes=[StrategySharpe("orb", 1.3)],
        ensemble_headline=EnsembleHeadline(cagr=1.5, mdd=0.25),
        regime_evaluations=[_eval("bull", "uptrend", 0.1)],
        stress_results=[_stress("luna", True)],
    )
    assert rep.ensemble_headline_pass is False
    assert rep.overall_pass is False


def test_ensemble_mdd_failure() -> None:
    rep = compose_phase3_report(
        strategy_sharpes=[StrategySharpe("orb", 1.3)],
        ensemble_headline=EnsembleHeadline(cagr=2.5, mdd=0.40),
        regime_evaluations=[_eval("bull", "uptrend", 0.1)],
        stress_results=[_stress("luna", True)],
    )
    assert rep.ensemble_headline_pass is False


def test_regime_failure_reports_labels() -> None:
    rep = compose_phase3_report(
        strategy_sharpes=[StrategySharpe("orb", 1.3)],
        ensemble_headline=EnsembleHeadline(cagr=2.5, mdd=0.25),
        regime_evaluations=[
            _eval("bull", "uptrend", 0.1),
            _eval("crash_down", "crash", -0.05),  # negative → fails break-even
        ],
        stress_results=[_stress("luna", True)],
    )
    assert rep.regimes_pass is False
    assert "crash_down" in rep.failing_regime_labels


def test_stress_failure_reports_labels() -> None:
    rep = compose_phase3_report(
        strategy_sharpes=[StrategySharpe("orb", 1.3)],
        ensemble_headline=EnsembleHeadline(cagr=2.5, mdd=0.25),
        regime_evaluations=[_eval("bull", "uptrend", 0.1)],
        stress_results=[_stress("luna", False)],
    )
    assert rep.stress_pass is False
    assert rep.failing_stress_labels == ("luna",)
    assert rep.overall_pass is False


def test_summary_lines_contain_verdict() -> None:
    rep = compose_phase3_report(
        strategy_sharpes=[StrategySharpe("orb", 1.3)],
        ensemble_headline=EnsembleHeadline(cagr=2.5, mdd=0.25),
        regime_evaluations=[_eval("bull", "uptrend", 0.1)],
        stress_results=[_stress("luna", True)],
    )
    lines = rep.summary_lines()
    assert any("PASS" in ln for ln in lines)
    assert any("Strategy Sharpe" in ln for ln in lines)
    assert any("Ensemble CAGR" in ln for ln in lines)
