"""Top-level backtest job entrypoint — portfolio form (Phase 5).

Lives at module scope so the multiprocessing ``spawn`` worker can pickle and
import it cleanly. Reads the run row via the repo, loads a PortfolioBars,
builds a strategies dict and per-symbol params, runs a PortfolioEngine
(optionally wrapped in PortfolioRiskSimulator), writes portfolio artifacts
to ``<data_root>/run_artifacts/<run_id>/``, and updates the DB row.

Progress messages are pushed into the supplied ``progress_queue`` — the
manager-backed queue owned by ``kairos.dashboard.api.progress``.

Single-symbol runs are expressed as ``weights={X: 1.0}`` on the request side;
there is no single-symbol code path here anymore.
"""
from __future__ import annotations

import logging
import traceback
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from kairos.dashboard.api import repo

log = logging.getLogger("kairos.dashboard.jobs")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _put(queue: Any, run_id: str, message: dict[str, Any]) -> None:
    """Publish a progress message, swallowing queue errors.

    A dead queue shouldn't crash the worker — better to keep running and
    update the DB row than to fail on a detached subscriber.
    """
    if queue is None:
        return
    try:
        queue.put({"run_id": run_id, "message": message})
    except Exception:
        log.exception("progress queue put failed for run %s", run_id)


def _build_engine(name: str) -> Any:
    """Portfolio engine factory. The name is the one persisted on the row."""
    if name == "vectorbt":
        from kairos.backtest.engine_portfolio_vbt import PortfolioVectorbtEngine

        return PortfolioVectorbtEngine()
    if name == "backtrader":
        from kairos.backtest.engine_portfolio_bt import PortfolioBacktraderEngine

        return PortfolioBacktraderEngine()
    raise ValueError(f"unknown engine: {name!r}")


# WHY fallback spec rather than load-from-exchangeInfo: the portfolio jobs
# path must keep running even when the exchangeInfo cache is stale. Defaults
# below mirror kairos.backtest.gate_runner._fallback_spec; keep in sync.
_FALLBACK_DEFAULTS: dict[str, tuple[Decimal, Decimal, Decimal, Decimal]] = {
    "BTCUSDT": (Decimal("0.1"), Decimal("0.001"), Decimal("0.001"), Decimal("5")),
    "ETHUSDT": (Decimal("0.01"), Decimal("0.001"), Decimal("0.001"), Decimal("5")),
    "SOLUSDT": (Decimal("0.001"), Decimal("1"), Decimal("1"), Decimal("5")),
}


def _fallback_spec(symbol: str) -> Any:
    from kairos.backtest.protocols import SymbolSpec

    tick, step, min_qty, min_notional = _FALLBACK_DEFAULTS.get(
        symbol, (Decimal("0.01"), Decimal("0.001"), Decimal("0.001"), Decimal("5"))
    )
    return SymbolSpec(
        symbol=symbol,
        tick_size=tick,
        step_size=step,
        min_qty=min_qty,
        min_notional=min_notional,
    )


def _artifacts_dir(run_id: str) -> Path:
    # Import guard: repo._data_root is private-ish but safe re-use.
    root = repo._data_root()  # noqa: SLF001 — intentional internal use
    d = root / "run_artifacts" / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _iso_utc(ts: object) -> str:
    """Format any timestamp-ish object as ISO-8601 UTC (``...Z``)."""
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    else:
        t = t.tz_convert("UTC")
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_equity(artifacts_dir: Path, equity: pd.Series) -> Path:
    out = artifacts_dir / "equity.parquet"
    df = pd.DataFrame(
        {
            "timestamp": [_iso_utc(t) for t in equity.index],
            "equity": [float(v) for v in equity.values],
        }
    )
    df.to_parquet(out, index=False)
    return out


def _write_equity_per_symbol(
    artifacts_dir: Path, per_symbol: dict[str, pd.Series]
) -> Path:
    """Wide frame: timestamp + one column per symbol."""
    out = artifacts_dir / "equity_per_symbol.parquet"
    if not per_symbol:
        df = pd.DataFrame({"timestamp": []})
        df.to_parquet(out, index=False)
        return out
    # Union index across all symbols — in practice engines return identical
    # indexes, but be defensive so a per-symbol re-slice doesn't corrupt output.
    first_idx = next(iter(per_symbol.values())).index
    data: dict[str, list[Any]] = {"timestamp": [_iso_utc(t) for t in first_idx]}
    for sym in sorted(per_symbol.keys()):
        series = per_symbol[sym].reindex(first_idx)
        data[sym] = [float(v) for v in series.values]
    df = pd.DataFrame(data)
    df.to_parquet(out, index=False)
    return out


def _write_trades(artifacts_dir: Path, trades: pd.DataFrame) -> Path:
    """Write trades preserving the leading ``symbol`` column."""
    out = artifacts_dir / "trades.parquet"
    df = trades.copy()
    if "symbol" in df.columns:
        cols = ["symbol"] + [c for c in df.columns if c != "symbol"]
        df = df[cols]
    df.to_parquet(out, index=False)
    return out


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def run_backtest_job(run_id: str, progress_queue: Any) -> None:
    """Top-level pool target. Must be importable + picklable.

    Exceptions are swallowed and surfaced via repo.mark_failed + a progress
    ``error`` message so the worker pool keeps running the next job.
    """
    try:
        _put(progress_queue, run_id, {
            "type": "progress", "pct": 0.0, "stage": "load",
            "log": "loading run row",
        })
        detail = repo.get_run(run_id)
        if detail is None:
            raise RuntimeError(f"run {run_id!r} not found in DB")

        repo.mark_running(run_id)

        from kairos.data.load import load_portfolio

        start_ts = pd.Timestamp(detail.start)
        if start_ts.tzinfo is None:
            start_ts = start_ts.tz_localize("UTC")
        else:
            start_ts = start_ts.tz_convert("UTC")
        end_ts = pd.Timestamp(detail.end)
        if end_ts.tzinfo is None:
            end_ts = end_ts.tz_localize("UTC")
        else:
            end_ts = end_ts.tz_convert("UTC")

        weights = {str(k): float(v) for k, v in detail.weights.items()}
        symbols = sorted(weights.keys())

        portfolio = load_portfolio(
            symbols=symbols,
            interval=detail.interval,  # type: ignore[arg-type]
            start=start_ts,
            end=end_ts,
            weights=weights,
        )

        # detail.strategies is dict[symbol, name]; params live on
        # detail.strategy_params (dict[symbol, dict]).
        from kairos.strategies import registry

        strategies: dict[str, Any] = {}
        params: dict[str, Any] = {}
        for sym in symbols:
            name = detail.strategies[sym]
            strategies[sym] = registry.build(name)
            params[sym] = dict(detail.strategy_params.get(sym, {}))

        symbol_specs = {sym: _fallback_spec(sym) for sym in symbols}

        base_engine = _build_engine(detail.engine)

        from kairos.backtest.protocols import CostModel

        if detail.apply_risk_simulator:
            from kairos.backtest.protocols import RiskRules
            from kairos.backtest.risk_simulator import PortfolioRiskSimulator

            runner: Any = PortfolioRiskSimulator(base_engine, RiskRules())
        else:
            runner = base_engine

        _put(progress_queue, run_id, {
            "type": "progress", "pct": 30.0, "stage": "run",
            "log": f"engine={detail.engine} symbols={symbols}",
        })

        result = runner.run(
            strategies=strategies,
            portfolio=portfolio,
            params=params,
            costs=CostModel(),
            symbol_specs=symbol_specs,
            initial_cash=Decimal(str(detail.initial_cash)),
        )

        artifacts_dir = _artifacts_dir(run_id)
        _write_equity(artifacts_dir, result.equity_curve)
        _write_equity_per_symbol(artifacts_dir, dict(result.per_symbol_equity))
        _write_trades(artifacts_dir, result.trades)

        metrics = {str(k): float(v) for k, v in result.metrics.items()}
        per_symbol_metrics = {
            str(sym): {str(k): float(v) for k, v in m.items()}
            for sym, m in result.per_symbol_metrics.items()
        }

        repo.mark_done(
            run_id,
            metrics=metrics,
            per_symbol_metrics=per_symbol_metrics,
            artifacts_dir=str(artifacts_dir),
            trades_count=int(len(result.trades)),
        )
        _put(progress_queue, run_id, {
            "type": "done", "metrics": metrics,
        })
    except Exception as exc:  # noqa: BLE001 — we re-surface via DB + queue
        tb = traceback.format_exc()
        log.exception("run_backtest_job failed for %s", run_id)
        try:
            repo.mark_failed(run_id, f"{exc}\n{tb}")
        except Exception:
            log.exception("mark_failed also failed for %s", run_id)
        _put(progress_queue, run_id, {
            "type": "error", "message": str(exc),
        })


__all__ = ["run_backtest_job"]
