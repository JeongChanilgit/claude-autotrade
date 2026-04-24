"""Unit tests for the PDF report export endpoint — Phase 5 portfolio shape.

Monkeypatches repo at the reports module level — never touches SQLite or the
on-disk parquet artifacts.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from kairos.dashboard.api import reports as reports_module
from kairos.dashboard.api.dtos import EquityPayloadTotal, RunDetail
from kairos.dashboard.api.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _fake_detail(run_id: str = "run_pdf", metrics: dict[str, float] | None = None) -> RunDetail:
    base: dict[str, Any] = {
        "id": run_id,
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "strategies": {"BTCUSDT": "orb", "ETHUSDT": "orb"},
        "weights": {"BTCUSDT": 0.6, "ETHUSDT": 0.4},
        "interval": "15m",
        "start": "2024-01-01T00:00:00Z",
        "end": "2024-02-01T00:00:00Z",
        "engine": "vectorbt",
        "status": "done",
        "created_at": "2024-02-02T00:00:00Z",
        "strategy_params": {"BTCUSDT": {"or_minutes": 15}, "ETHUSDT": {"or_minutes": 15}},
        "initial_cash": 10_000.0,
        "apply_risk_simulator": True,
        "metrics": metrics,
        "per_symbol_metrics": None,
        "error": None,
    }
    return RunDetail.model_validate(base)


def test_report_pdf_returns_pdf_bytes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    metrics = {
        "cagr": 0.25,
        "mdd": -0.12,
        "sharpe": 1.4,
        "calmar": 2.0,
        "profit_factor": 1.6,
        "win_rate": 0.55,
        "total_trades": 42,
    }
    monkeypatch.setattr(
        reports_module.repo, "get_run", lambda rid: _fake_detail(rid, metrics=metrics)
    )
    monkeypatch.setattr(
        reports_module.repo,
        "load_equity",
        lambda _rid, *, per_symbol=False: EquityPayloadTotal(
            timestamps=[
                "2024-01-01T00:00:00Z",
                "2024-01-02T00:00:00Z",
                "2024-01-03T00:00:00Z",
            ],
            equity=[10000.0, 10150.0, 10220.0],
        ),
    )

    resp = client.get("/api/runs/run_pdf/report.pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/pdf")
    body = resp.content
    assert body[:4] == b"%PDF"
    # Must be non-trivial — a real PDF ends with %%EOF somewhere in the tail.
    assert b"%%EOF" in body[-32:]


def test_report_pdf_missing_run_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reports_module.repo, "get_run", lambda _rid: None)
    # load_equity must not be called when the run is missing.
    monkeypatch.setattr(
        reports_module.repo,
        "load_equity",
        lambda _rid, *, per_symbol=False: pytest.fail(
            "load_equity must not be called for missing runs"
        ),
    )

    resp = client.get("/api/runs/run_does_not_exist/report.pdf")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "RUN_NOT_FOUND"
