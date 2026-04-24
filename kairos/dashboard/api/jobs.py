"""Top-level backtest job entrypoint.

Lives at module scope so the multiprocessing ``spawn`` worker can pickle and
import it cleanly. Reads the run row via the repo, runs data load + engine
(optionally wrapped in the RiskSimulator), writes artifacts to
``<data_root>/run_artifacts/<run_id>/``, and updates the DB row.

Progress messages are pushed into the supplied ``progress_queue`` — the
manager-backed queue owned by ``kairos.dashboard.api.progress``.
"""
from __future__ import annotations

import json
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


def _build_strategy(name: str) -> Any:
    """Strategy selection mirrors the frozen CreateRunRequest enum."""
    if name == "orb":
        from kairos.strategies.orb import OrbStrategy

        return OrbStrategy()
    if name == "funding_counter":
        from kairos.strategies.funding import FundingCounterStrategy

        return FundingCounterStrategy()
    if name == "vol_compression":
        from kairos.strategies.vol_compression import VolCompressionStrategy

        return VolCompressionStrategy()
    if name == "ensemble":
        from kairos.strategies.ensemble import build_default_ensemble

        return build_default_ensemble()
    raise ValueError(f"unknown strategy: {name!r}")


def _build_engine(name: str) -> Any:
    if name == "vectorbt":
        from kairos.backtest.engine_vbt import VectorbtEngine

        return VectorbtEngine()
    if name == "backtrader":
        from kairos.backtest.engine_bt import BacktraderEngine

        return BacktraderEngine()
    raise ValueError(f"unknown engine: {name!r}")


def _load_symbol_spec(symbol: str) -> Any:
    from kairos.backtest.protocols import SymbolSpec

    specs_path = (
        Path(__file__).resolve().parents[2]
        / "backtest"
        / "_symbol_specs.json"
    )
    with specs_path.open("r", encoding="utf-8") as fh:
        specs = json.load(fh)
    if symbol not in specs:
        raise ValueError(f"no symbol spec for {symbol!r}")
    raw = specs[symbol]
    return SymbolSpec(
        symbol=symbol,
        tick_size=Decimal(str(raw["tick_size"])),
        step_size=Decimal(str(raw["step_size"])),
        min_qty=Decimal(str(raw["min_qty"])),
        min_notional=Decimal(str(raw["min_notional"])),
    )


def _artifacts_dir(run_id: str) -> Path:
    # Import guard: repo._data_root is private-ish but safe re-use.
    root = repo._data_root()  # noqa: SLF001 — intentional internal use
    d = root / "run_artifacts" / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_equity(run_id: str, equity: pd.Series) -> Path:
    out = _artifacts_dir(run_id) / "equity.parquet"
    ts = [pd.Timestamp(t).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
          if pd.Timestamp(t).tzinfo else pd.Timestamp(t).tz_localize("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
          for t in equity.index]
    df = pd.DataFrame({"timestamp": ts, "equity": [float(v) for v in equity.values]})
    df.to_parquet(out, index=False)
    return out


def _write_trades(run_id: str, trades: pd.DataFrame) -> Path:
    out = _artifacts_dir(run_id) / "trades.parquet"
    trades.to_parquet(out, index=False)
    return out


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def run_backtest_job(run_id: str, progress_queue: Any) -> None:
    """Top-level pool target. Must be importable + picklable."""
    try:
        _put(progress_queue, run_id, {
            "type": "progress", "pct": 0.0, "stage": "load_row",
            "log": "loading run row",
        })
        detail = repo.get_run(run_id)
        if detail is None:
            raise RuntimeError(f"run {run_id!r} not found in DB")

        repo.mark_running(run_id)
        _put(progress_queue, run_id, {
            "type": "progress", "pct": 5.0, "stage": "load_data",
            "log": f"loading bars for {detail.symbol} {detail.interval}",
        })

        from kairos.data.load import load_bars

        start_ts = pd.Timestamp(detail.start).tz_convert("UTC") if pd.Timestamp(
            detail.start
        ).tzinfo else pd.Timestamp(detail.start).tz_localize("UTC")
        end_ts = pd.Timestamp(detail.end).tz_convert("UTC") if pd.Timestamp(
            detail.end
        ).tzinfo else pd.Timestamp(detail.end).tz_localize("UTC")

        data = load_bars(detail.symbol, detail.interval, start_ts, end_ts)
        _put(progress_queue, run_id, {
            "type": "progress", "pct": 25.0, "stage": "engine_start",
            "log": f"engine={detail.engine} strategy={detail.strategy}",
        })

        strategy = _build_strategy(detail.strategy)
        base_engine = _build_engine(detail.engine)
        symbol_spec = _load_symbol_spec(detail.symbol)

        from kairos.backtest.protocols import CostModel

        params = dict(detail.params)

        if detail.apply_risk_simulator:
            from kairos.backtest.protocols import RiskRules
            from kairos.backtest.risk_simulator import RiskSimulator

            runner = RiskSimulator(base_engine, RiskRules())
        else:
            runner = base_engine

        result = runner.run(
            strategy=strategy,
            data=data,
            params=params,
            costs=CostModel(),
            symbol_spec=symbol_spec,
            initial_cash=Decimal(str(detail.initial_cash)),
        )

        _put(progress_queue, run_id, {
            "type": "progress", "pct": 80.0, "stage": "engine_done",
            "log": f"{len(result.trades)} trades",
        })

        _put(progress_queue, run_id, {
            "type": "progress", "pct": 90.0, "stage": "metrics",
            "log": "metrics computed",
        })

        artifacts_dir = _artifacts_dir(run_id)
        _write_equity(run_id, result.equity_curve)
        _write_trades(run_id, result.trades)

        _put(progress_queue, run_id, {
            "type": "progress", "pct": 95.0, "stage": "artifacts",
            "log": "artifacts written",
        })

        metrics = {str(k): float(v) for k, v in result.metrics.items()}
        repo.mark_done(
            run_id,
            metrics=metrics,
            equity_path=str(artifacts_dir),
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
