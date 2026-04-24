"""REST routes for the Kairos dashboard.

Exposes ``api_router`` under the ``/api`` prefix. Every endpoint shape is
pinned by docs/phase4/API.md; see dtos.py for the frozen Pydantic models.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, status
from fastapi.responses import JSONResponse

from kairos.dashboard.api import progress, repo, worker
from kairos.dashboard.api.dtos import (
    CompareReport,
    CreateRunRequest,
    EquityDiff,
    GateReportSummary,
    MetricDiff,
    RunDetail,
    RunStatus,
    RunSummary,
)
from kairos.dashboard.api.errors import KairosAPIError

api_router = APIRouter(prefix="/api")

# Repo gate-reports scan directory. Tests rebind this via monkeypatch.
REPORTS_DIR: Path = Path("reports")


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


@api_router.get("/runs")
def list_runs(
    limit: int = Query(100, ge=1, le=1000),
    status_filter: str | None = Query(None, alias="status"),
) -> dict[str, list[RunSummary]]:
    if status_filter is not None and status_filter not in (
        "pending", "running", "done", "failed"
    ):
        raise KairosAPIError(
            "VALIDATION_ERROR",
            f"invalid status {status_filter!r}",
            status=422,
        )
    runs = repo.list_runs(limit=limit, status=status_filter)
    return {"runs": runs}


@api_router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
def create_run(req: CreateRunRequest) -> dict[str, str]:
    run_id = repo.create_run(req)
    worker.submit(run_id)
    return {"id": run_id, "status": "pending"}


@api_router.get("/runs/{run_id}")
def get_run(run_id: str) -> RunDetail:
    detail = repo.get_run(run_id)
    if detail is None:
        raise KairosAPIError("RUN_NOT_FOUND", f"run {run_id!r} not found", status=404)
    return detail


@api_router.get("/runs/{run_id}/trades")
def get_trades(
    run_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(1000, ge=0, le=100_000),
) -> dict[str, Any]:
    # Confirm the run exists so clients get RUN_NOT_FOUND rather than an
    # empty page when they typo the id.
    if repo.get_run(run_id) is None:
        raise KairosAPIError("RUN_NOT_FOUND", f"run {run_id!r} not found", status=404)
    trades, total = repo.load_trades(run_id, offset, limit)
    return {"trades": trades, "total": total}


@api_router.get("/runs/{run_id}/equity")
def get_equity(run_id: str) -> dict[str, list[Any]]:
    if repo.get_run(run_id) is None:
        raise KairosAPIError("RUN_NOT_FOUND", f"run {run_id!r} not found", status=404)
    ts, eq = repo.load_equity(run_id)
    if not ts:
        raise KairosAPIError(
            "DATA_UNAVAILABLE",
            f"no equity artifact for run {run_id!r}",
            status=404,
        )
    return {"timestamps": ts, "equity": eq}


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------


class _CompareBody(dict):
    """Placeholder for typing only; real validation happens in the handler."""


def _metric_diff(a: dict[str, Any], b: dict[str, Any]) -> dict[str, MetricDiff]:
    out: dict[str, MetricDiff] = {}
    for key in a.keys() & b.keys():
        av = a[key]
        bv = b[key]
        if not isinstance(av, (int, float)) or not isinstance(bv, (int, float)):
            continue
        if isinstance(av, bool) or isinstance(bv, bool):
            # bool is a subclass of int — exclude it, it's not a metric.
            continue
        af = float(av)
        bf = float(bv)
        denom = max(abs(af), 1e-12)
        out[key] = MetricDiff(a=af, b=bf, rel_diff=(bf - af) / denom)
    return out


def _equity_intersection(
    ts_a: list[str], eq_a: list[float],
    ts_b: list[str], eq_b: list[float],
) -> EquityDiff:
    """Align two equity curves on their ISO timestamp intersection.

    We build index dicts rather than a pandas merge to stay O(n) without the
    pandas datetime parsing overhead — the LTTB-downsampled strings are
    already canonical from load_equity.
    """
    idx_a = {t: v for t, v in zip(ts_a, eq_a)}
    idx_b = {t: v for t, v in zip(ts_b, eq_b)}
    common = sorted(idx_a.keys() & idx_b.keys())
    return EquityDiff(
        timestamps=common,
        a=[float(idx_a[t]) for t in common],
        b=[float(idx_b[t]) for t in common],
    )


def _summary_of(detail: RunDetail) -> RunSummary:
    return RunSummary(**{k: getattr(detail, k) for k in RunSummary.model_fields})


@api_router.post("/compare")
def compare(body: dict[str, Any]) -> CompareReport:
    ids = body.get("run_ids")
    if not isinstance(ids, list) or len(ids) != 2 or not all(
        isinstance(x, str) for x in ids
    ):
        raise KairosAPIError(
            "VALIDATION_ERROR",
            "run_ids must be a list of exactly two run id strings",
            status=422,
        )
    a_id, b_id = ids[0], ids[1]
    a_detail = repo.get_run(a_id)
    if a_detail is None:
        raise KairosAPIError("RUN_NOT_FOUND", f"run {a_id!r} not found", status=404)
    b_detail = repo.get_run(b_id)
    if b_detail is None:
        raise KairosAPIError("RUN_NOT_FOUND", f"run {b_id!r} not found", status=404)

    a_metrics = a_detail.metrics or {}
    b_metrics = b_detail.metrics or {}
    mdiff = _metric_diff(a_metrics, b_metrics)

    # Equity curves may be missing while still pending/running — treat as empty.
    ts_a, eq_a = repo.load_equity(a_id)
    ts_b, eq_b = repo.load_equity(b_id)
    ediff = _equity_intersection(ts_a, eq_a, ts_b, eq_b)

    return CompareReport(
        a=_summary_of(a_detail),
        b=_summary_of(b_detail),
        metric_diff=mdiff,
        equity_diff=ediff,
    )


# ---------------------------------------------------------------------------
# Gate reports
# ---------------------------------------------------------------------------


def _gate_summary(name: str, data: dict[str, Any]) -> GateReportSummary:
    symbol = str(data.get("symbol", ""))
    interval = str(data.get("interval", ""))
    # gate_runner emits window.{start,end}; fall back to top-level for forward
    # compatibility with a flatter schema.
    window = data.get("window") or {}
    start = str(window.get("start", data.get("start", "")))
    end = str(window.get("end", data.get("end", "")))
    overall_pass = bool((data.get("gate") or {}).get("overall_pass", False))
    return GateReportSummary(
        name=name,
        symbol=symbol,
        interval=interval,
        start=start,
        end=end,
        overall_pass=overall_pass,
    )


@api_router.get("/gate-reports")
def gate_reports() -> dict[str, list[GateReportSummary]]:
    reports: list[GateReportSummary] = []
    if not REPORTS_DIR.exists():
        return {"reports": reports}
    for path in sorted(REPORTS_DIR.glob("phase3_gate_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        reports.append(_gate_summary(path.stem, data))
    return {"reports": reports}


@api_router.get("/gate-reports/{name}")
def gate_report_detail(name: str) -> JSONResponse:
    # Traversal guard: reject any path separator or parent ref before we
    # touch the filesystem.
    if "/" in name or "\\" in name or ".." in name or name in ("", "."):
        raise KairosAPIError(
            "VALIDATION_ERROR",
            "invalid report name",
            status=400,
        )
    path = REPORTS_DIR / f"{name}.json"
    if not path.exists() or not path.is_file():
        raise KairosAPIError(
            "RUN_NOT_FOUND",
            f"gate report {name!r} not found",
            status=404,
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KairosAPIError(
            "DATA_UNAVAILABLE",
            f"could not read gate report {name!r}: {exc}",
            status=500,
        ) from exc
    return JSONResponse(content=data)


__all__ = ["REPORTS_DIR", "api_router"]
