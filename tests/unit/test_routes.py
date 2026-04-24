"""Unit tests for kairos.dashboard.api.routes.

Repo + worker are monkeypatched at the routes module level so these tests
never touch SQLite or a real multiprocessing pool.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kairos.dashboard.api import routes
from kairos.dashboard.api.dtos import RunDetail, RunSummary, Trade
from kairos.dashboard.api.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _summary(run_id: str = "run_aaa", **overrides: Any) -> RunSummary:
    base: dict[str, Any] = {
        "id": run_id,
        "strategy": "orb",
        "symbol": "BTCUSDT",
        "interval": "1m",
        "start": "2024-01-01T00:00:00Z",
        "end": "2024-02-01T00:00:00Z",
        "engine": "vectorbt",
        "status": "done",
        "created_at": "2024-02-02T00:00:00Z",
    }
    base.update(overrides)
    return RunSummary.model_validate(base)


def _detail(run_id: str = "run_aaa", metrics: dict[str, float] | None = None,
            **overrides: Any) -> RunDetail:
    summary = _summary(run_id, **overrides).model_dump()
    summary["params"] = {"lookback": 30}
    summary["initial_cash"] = 10_000.0
    summary["apply_risk_simulator"] = True
    summary["metrics"] = metrics
    summary["error"] = None
    return RunDetail.model_validate(summary)


# ---------------------------------------------------------------------------
# /api/runs
# ---------------------------------------------------------------------------


def test_list_runs_returns_wrapped_array(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict[str, Any] = {}

    def fake_list_runs(limit: int = 100, status: str | None = None):
        calls["args"] = (limit, status)
        return [_summary("run_1"), _summary("run_2", status="pending")]

    monkeypatch.setattr(routes.repo, "list_runs", fake_list_runs)
    resp = client.get("/api/runs?limit=5&status=pending")
    assert resp.status_code == 200
    body = resp.json()
    assert "runs" in body
    assert len(body["runs"]) == 2
    assert body["runs"][0]["id"] == "run_1"
    assert calls["args"] == (5, "pending")


def test_list_runs_rejects_unknown_status(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes.repo, "list_runs", lambda **_kw: [])
    resp = client.get("/api/runs?status=bogus")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_run_returns_202_and_submits_once(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    submitted: list[str] = []

    def fake_create(_req):
        return "run_new"

    def fake_submit(run_id: str):
        submitted.append(run_id)
        return object()

    monkeypatch.setattr(routes.repo, "create_run", fake_create)
    monkeypatch.setattr(routes.worker, "submit", fake_submit)

    body = {
        "strategy": "orb",
        "symbol": "BTCUSDT",
        "interval": "1m",
        "start": "2024-01-01T00:00:00Z",
        "end": "2024-02-01T00:00:00Z",
        "engine": "vectorbt",
        "params": {"lookback": 30},
        "initial_cash": 10_000.0,
        "apply_risk_simulator": True,
    }
    resp = client.post("/api/runs", json=body)
    assert resp.status_code == 202
    assert resp.json() == {"id": "run_new", "status": "pending"}
    assert submitted == ["run_new"]


def test_create_run_validation_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Should never get this far, but guard against accidental side effects.
    monkeypatch.setattr(routes.repo, "create_run",
                        lambda _r: pytest.fail("create_run should not be called"))
    monkeypatch.setattr(routes.worker, "submit",
                        lambda _i: pytest.fail("submit should not be called"))

    body = {
        "strategy": "not-a-real-strategy",  # fails Literal enum
        "symbol": "BTCUSDT",
        "interval": "1m",
        "start": "2024-01-01T00:00:00Z",
        "end": "2024-02-01T00:00:00Z",
        "engine": "vectorbt",
    }
    resp = client.post("/api/runs", json=body)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_get_run_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes.repo, "get_run", lambda _rid: None)
    resp = client.get("/api/runs/run_missing")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "RUN_NOT_FOUND"


def test_get_run_ok(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        routes.repo, "get_run",
        lambda rid: _detail(rid, metrics={"sharpe": 1.5, "cagr": 0.2}),
    )
    resp = client.get("/api/runs/run_aaa")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "run_aaa"
    assert data["metrics"]["sharpe"] == 1.5


# ---------------------------------------------------------------------------
# /api/runs/{id}/trades and /equity
# ---------------------------------------------------------------------------


def _trade(**overrides: Any) -> Trade:
    base = {
        "entry_time": "2024-01-01T00:00:00Z",
        "exit_time": "2024-01-01T01:00:00Z",
        "side": 1,
        "qty": 0.1,
        "entry_price": 40_000.0,
        "exit_price": 40_100.0,
        "gross_pnl": 10.0,
        "fees": 0.5,
        "slippage": 0.1,
        "funding_cost": 0.0,
        "net_pnl": 9.4,
        "bars_held": 60,
        "exit_reason": "signal",
    }
    base.update(overrides)
    return Trade.model_validate(base)


def test_trades_pagination_passthrough(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr(routes.repo, "get_run", lambda rid: _detail(rid))

    def fake_load_trades(run_id: str, offset: int, limit: int):
        captured["args"] = (run_id, offset, limit)
        return [_trade()], 42

    monkeypatch.setattr(routes.repo, "load_trades", fake_load_trades)

    resp = client.get("/api/runs/run_aaa/trades?offset=10&limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 42
    assert len(data["trades"]) == 1
    assert captured["args"] == ("run_aaa", 10, 5)


def test_trades_unknown_run_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes.repo, "get_run", lambda _rid: None)
    resp = client.get("/api/runs/run_missing/trades")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "RUN_NOT_FOUND"


def test_equity_passthrough(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes.repo, "get_run", lambda rid: _detail(rid))
    monkeypatch.setattr(
        routes.repo, "load_equity",
        lambda rid: (["2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z"], [100.0, 101.0]),
    )
    resp = client.get("/api/runs/run_aaa/equity")
    assert resp.status_code == 200
    assert resp.json() == {
        "timestamps": ["2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z"],
        "equity": [100.0, 101.0],
    }


def test_equity_no_artifact_yields_data_unavailable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes.repo, "get_run", lambda rid: _detail(rid))
    monkeypatch.setattr(routes.repo, "load_equity", lambda _rid: ([], []))
    resp = client.get("/api/runs/run_aaa/equity")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "DATA_UNAVAILABLE"


# ---------------------------------------------------------------------------
# /api/compare
# ---------------------------------------------------------------------------


def test_compare_happy_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    details = {
        "run_a": _detail("run_a", metrics={"sharpe": 1.0, "cagr": 0.1}),
        "run_b": _detail("run_b", metrics={"sharpe": 2.0, "cagr": 0.2}),
    }
    monkeypatch.setattr(routes.repo, "get_run", lambda rid: details.get(rid))

    equities = {
        "run_a": (
            ["2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z", "2024-01-03T00:00:00Z"],
            [100.0, 110.0, 120.0],
        ),
        "run_b": (
            ["2024-01-02T00:00:00Z", "2024-01-03T00:00:00Z", "2024-01-04T00:00:00Z"],
            [200.0, 210.0, 220.0],
        ),
    }
    monkeypatch.setattr(routes.repo, "load_equity", lambda rid: equities[rid])

    resp = client.post("/api/compare", json={"run_ids": ["run_a", "run_b"]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["a"]["id"] == "run_a"
    assert data["b"]["id"] == "run_b"
    # sharpe: (2-1)/max(1,eps) = 1.0
    assert data["metric_diff"]["sharpe"]["rel_diff"] == pytest.approx(1.0)
    # Intersection of timestamps is the two middle points.
    assert data["equity_diff"]["timestamps"] == [
        "2024-01-02T00:00:00Z", "2024-01-03T00:00:00Z",
    ]
    assert data["equity_diff"]["a"] == [110.0, 120.0]
    assert data["equity_diff"]["b"] == [200.0, 210.0]


def test_compare_missing_run_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        routes.repo, "get_run",
        lambda rid: _detail(rid) if rid == "run_a" else None,
    )
    monkeypatch.setattr(routes.repo, "load_equity", lambda _rid: ([], []))
    resp = client.post("/api/compare", json={"run_ids": ["run_a", "run_missing"]})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "RUN_NOT_FOUND"


def test_compare_rejects_bad_body(client: TestClient) -> None:
    resp = client.post("/api/compare", json={"run_ids": ["only_one"]})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# /api/gate-reports
# ---------------------------------------------------------------------------


def _write_gate_report(dir_: Path, name: str, *, symbol: str, overall_pass: bool) -> None:
    data = {
        "symbol": symbol,
        "interval": "15m",
        "window": {
            "start": "2020-10-01T00:00:00+00:00",
            "end": "2024-08-10T00:00:00+00:00",
        },
        "gate": {"overall_pass": overall_pass},
    }
    (dir_ / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")


def test_gate_reports_list(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes, "REPORTS_DIR", tmp_path)
    _write_gate_report(tmp_path, "phase3_gate_btc", symbol="BTCUSDT", overall_pass=True)
    _write_gate_report(tmp_path, "phase3_gate_eth", symbol="ETHUSDT", overall_pass=False)
    # Non-matching file — must be ignored.
    (tmp_path / "unrelated.json").write_text("{}", encoding="utf-8")

    resp = client.get("/api/gate-reports")
    assert resp.status_code == 200
    reports = resp.json()["reports"]
    names = sorted(r["name"] for r in reports)
    assert names == ["phase3_gate_btc", "phase3_gate_eth"]
    btc = next(r for r in reports if r["name"] == "phase3_gate_btc")
    assert btc["symbol"] == "BTCUSDT"
    assert btc["overall_pass"] is True
    assert btc["start"].startswith("2020-10-01")


def test_gate_report_detail_ok(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes, "REPORTS_DIR", tmp_path)
    _write_gate_report(tmp_path, "phase3_gate_btc", symbol="BTCUSDT", overall_pass=True)
    resp = client.get("/api/gate-reports/phase3_gate_btc")
    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "BTCUSDT"
    assert data["gate"]["overall_pass"] is True


def test_gate_report_detail_traversal_rejected(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes, "REPORTS_DIR", tmp_path)
    # Starlette normalises '..' path segments away before dispatch, so we
    # invoke the handler directly to confirm the sanitiser rejects the
    # dangerous name. (A client-side '..' is already neutralised by the
    # router; belt-and-braces on the handler matters for any caller that
    # bypasses the router or passes a crafted name through code.)
    from kairos.dashboard.api.errors import KairosAPIError

    with pytest.raises(KairosAPIError) as exc_info:
        routes.gate_report_detail("..")
    assert exc_info.value.code == "VALIDATION_ERROR"
    assert exc_info.value.status == 400

    with pytest.raises(KairosAPIError) as exc_info2:
        routes.gate_report_detail("foo/bar")
    assert exc_info2.value.code == "VALIDATION_ERROR"

    with pytest.raises(KairosAPIError) as exc_info3:
        routes.gate_report_detail("..\\evil")
    assert exc_info3.value.code == "VALIDATION_ERROR"


def test_gate_report_detail_missing_file(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes, "REPORTS_DIR", tmp_path)
    resp = client.get("/api/gate-reports/phase3_gate_nope")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "RUN_NOT_FOUND"
