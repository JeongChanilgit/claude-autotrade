"""Unit tests for kairos.dashboard.api.routes — Phase 5 portfolio shape.

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
from kairos.dashboard.api.dtos import (
    EquityPayloadPerSymbol,
    EquityPayloadTotal,
    RunDetail,
    RunSummary,
)
from kairos.dashboard.api.main import app


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _summary(run_id: str = "run_aaa", **overrides: Any) -> RunSummary:
    base: dict[str, Any] = {
        "id": run_id,
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "strategies": {"BTCUSDT": "orb", "ETHUSDT": "orb"},
        "weights": {"BTCUSDT": 0.5, "ETHUSDT": 0.5},
        "interval": "15m",
        "start": "2024-01-01T00:00:00Z",
        "end": "2024-02-01T00:00:00Z",
        "engine": "vectorbt",
        "status": "done",
        "created_at": "2024-02-02T00:00:00Z",
    }
    base.update(overrides)
    return RunSummary.model_validate(base)


def _detail(
    run_id: str = "run_aaa",
    metrics: dict[str, float] | None = None,
    **overrides: Any,
) -> RunDetail:
    base = _summary(run_id, **overrides).model_dump()
    base["strategy_params"] = {"BTCUSDT": {"or_minutes": 15}, "ETHUSDT": {"or_minutes": 15}}
    base["initial_cash"] = 10_000.0
    base["apply_risk_simulator"] = True
    base["metrics"] = metrics
    base["per_symbol_metrics"] = None
    base["error"] = None
    return RunDetail.model_validate(base)


def _valid_portfolio_body() -> dict[str, Any]:
    return {
        "strategies": {
            "BTCUSDT": {"name": "orb", "params": {}},
            "ETHUSDT": {"name": "orb", "params": {}},
        },
        "weights": {"BTCUSDT": 0.5, "ETHUSDT": 0.5},
        "interval": "15m",
        "start": "2024-01-01T00:00:00Z",
        "end": "2024-02-01T00:00:00Z",
        "engine": "vectorbt",
        "initial_cash": 10_000.0,
        "apply_risk_simulator": True,
    }


# ---------------------------------------------------------------------------
# /api/runs — listing
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
    assert body["runs"][0]["symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert calls["args"] == (5, "pending")


def test_list_runs_rejects_unknown_status(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes.repo, "list_runs", lambda **_kw: [])
    resp = client.get("/api/runs?status=bogus")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# /api/runs — create
# ---------------------------------------------------------------------------


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

    resp = client.post("/api/runs", json=_valid_portfolio_body())
    assert resp.status_code == 202
    assert resp.json() == {"id": "run_new", "status": "pending"}
    assert submitted == ["run_new"]


def test_create_run_rejects_weights_sum_not_one(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes.repo, "create_run",
                        lambda _r: pytest.fail("create_run should not be called"))
    monkeypatch.setattr(routes.worker, "submit",
                        lambda _i: pytest.fail("submit should not be called"))
    body = _valid_portfolio_body()
    body["weights"] = {"BTCUSDT": 0.7, "ETHUSDT": 0.7}
    resp = client.post("/api/runs", json=body)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_run_rejects_unknown_strategy_name(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes.repo, "create_run",
                        lambda _r: pytest.fail("create_run should not be called"))
    monkeypatch.setattr(routes.worker, "submit",
                        lambda _i: pytest.fail("submit should not be called"))
    body = _valid_portfolio_body()
    body["strategies"]["BTCUSDT"] = {"name": "does_not_exist", "params": {}}
    resp = client.post("/api/runs", json=body)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "STRATEGY_NOT_FOUND"


def test_create_run_rejects_mismatched_weights_strategies(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes.repo, "create_run",
                        lambda _r: pytest.fail("create_run should not be called"))
    monkeypatch.setattr(routes.worker, "submit",
                        lambda _i: pytest.fail("submit should not be called"))
    body = _valid_portfolio_body()
    # Weights cover a symbol that strategies does not.
    body["weights"] = {"BTCUSDT": 0.5, "SOLUSDT": 0.5}
    resp = client.post("/api/runs", json=body)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# /api/runs/{id}
# ---------------------------------------------------------------------------


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
    assert data["symbols"] == ["BTCUSDT", "ETHUSDT"]


# ---------------------------------------------------------------------------
# /api/runs/{id}/equity
# ---------------------------------------------------------------------------


def test_equity_default_is_portfolio_total(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes.repo, "get_run", lambda rid: _detail(rid))
    captured: dict[str, Any] = {}

    def fake_load_equity(run_id: str, *, per_symbol: bool = False):
        captured["args"] = (run_id, per_symbol)
        return EquityPayloadTotal(
            timestamps=["2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z"],
            equity=[100.0, 101.0],
        )

    monkeypatch.setattr(routes.repo, "load_equity", fake_load_equity)
    resp = client.get("/api/runs/run_aaa/equity")
    assert resp.status_code == 200
    assert resp.json() == {
        "timestamps": ["2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z"],
        "equity": [100.0, 101.0],
    }
    assert captured["args"] == ("run_aaa", False)


def test_equity_per_symbol_true(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes.repo, "get_run", lambda rid: _detail(rid))

    def fake_load_equity(run_id: str, *, per_symbol: bool = False):
        assert per_symbol is True
        return EquityPayloadPerSymbol(
            timestamps=["2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z"],
            per_symbol={"BTCUSDT": [100.0, 102.0], "ETHUSDT": [50.0, 49.5]},
        )

    monkeypatch.setattr(routes.repo, "load_equity", fake_load_equity)
    resp = client.get("/api/runs/run_aaa/equity?per_symbol=true")
    assert resp.status_code == 200
    data = resp.json()
    assert data["timestamps"] == ["2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z"]
    assert data["per_symbol"]["BTCUSDT"] == [100.0, 102.0]
    assert data["per_symbol"]["ETHUSDT"] == [50.0, 49.5]
    assert "equity" not in data


def test_equity_no_artifact_yields_data_unavailable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes.repo, "get_run", lambda rid: _detail(rid))
    monkeypatch.setattr(
        routes.repo, "load_equity",
        lambda _rid, *, per_symbol=False: EquityPayloadTotal(timestamps=[], equity=[]),
    )
    resp = client.get("/api/runs/run_aaa/equity")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "DATA_UNAVAILABLE"


# ---------------------------------------------------------------------------
# /api/runs/{id}/trades
# ---------------------------------------------------------------------------


def _trade_row(symbol: str = "BTCUSDT") -> dict[str, Any]:
    return {
        "symbol": symbol,
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


def test_trades_pagination_includes_symbol(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr(routes.repo, "get_run", lambda rid: _detail(rid))

    def fake_load_trades(run_id: str, offset: int, limit: int):
        captured["args"] = (run_id, offset, limit)
        return [_trade_row("BTCUSDT"), _trade_row("ETHUSDT")], 42

    monkeypatch.setattr(routes.repo, "load_trades", fake_load_trades)

    resp = client.get("/api/runs/run_aaa/trades?offset=10&limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 42
    assert len(data["trades"]) == 2
    assert data["trades"][0]["symbol"] == "BTCUSDT"
    assert data["trades"][1]["symbol"] == "ETHUSDT"
    assert captured["args"] == ("run_aaa", 10, 5)


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
        "run_a": EquityPayloadTotal(
            timestamps=["2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z", "2024-01-03T00:00:00Z"],
            equity=[100.0, 110.0, 120.0],
        ),
        "run_b": EquityPayloadTotal(
            timestamps=["2024-01-02T00:00:00Z", "2024-01-03T00:00:00Z", "2024-01-04T00:00:00Z"],
            equity=[200.0, 210.0, 220.0],
        ),
    }
    monkeypatch.setattr(
        routes.repo, "load_equity",
        lambda rid, *, per_symbol=False: equities[rid],
    )

    resp = client.post("/api/compare", json={"run_ids": ["run_a", "run_b"]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["a"]["id"] == "run_a"
    assert data["b"]["id"] == "run_b"
    assert data["metric_diff"]["sharpe"]["rel_diff"] == pytest.approx(1.0)
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
    monkeypatch.setattr(
        routes.repo, "load_equity",
        lambda _rid, *, per_symbol=False: EquityPayloadTotal(timestamps=[], equity=[]),
    )
    resp = client.post("/api/compare", json={"run_ids": ["run_a", "run_missing"]})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "RUN_NOT_FOUND"


def test_compare_rejects_bad_body(client: TestClient) -> None:
    resp = client.post("/api/compare", json={"run_ids": ["only_one"]})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# /api/strategies
# ---------------------------------------------------------------------------


def test_strategies_endpoint_returns_registered_entries(
    client: TestClient,
) -> None:
    resp = client.get("/api/strategies")
    assert resp.status_code == 200
    body = resp.json()
    assert "strategies" in body
    names = {entry["name"] for entry in body["strategies"]}
    # All four Phase 5 strategies must be discoverable via the registry.
    assert {"orb", "funding_counter", "vol_compression", "ensemble"}.issubset(names)
    # Shape sanity: each entry carries the contract fields.
    for entry in body["strategies"]:
        assert "label" in entry
        assert "default_params" in entry
        assert "param_schema" in entry
        assert "supports_funding" in entry


# ---------------------------------------------------------------------------
# /api/gate-reports
# ---------------------------------------------------------------------------


def _write_gate_report(
    dir_: Path,
    name: str,
    *,
    symbol: str | None = None,
    symbols: list[str] | None = None,
    overall_pass: bool,
) -> None:
    data: dict[str, Any] = {
        "interval": "15m",
        "window": {
            "start": "2020-10-01T00:00:00+00:00",
            "end": "2024-08-10T00:00:00+00:00",
        },
        "gate": {"overall_pass": overall_pass},
    }
    if symbols is not None:
        data["symbols"] = symbols
    if symbol is not None:
        data["symbol"] = symbol
    (dir_ / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")


def test_gate_reports_list_handles_both_shapes(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes, "REPORTS_DIR", tmp_path)
    # Old shape: single `symbol`.
    _write_gate_report(tmp_path, "phase3_gate_btc", symbol="BTCUSDT", overall_pass=True)
    # New portfolio shape: `symbols` list — route should surface the first.
    _write_gate_report(
        tmp_path, "phase3_gate_portfolio",
        symbols=["ETHUSDT", "SOLUSDT"], overall_pass=False,
    )
    # Non-matching file — must be ignored.
    (tmp_path / "unrelated.json").write_text("{}", encoding="utf-8")

    resp = client.get("/api/gate-reports")
    assert resp.status_code == 200
    reports = resp.json()["reports"]
    names = sorted(r["name"] for r in reports)
    assert names == ["phase3_gate_btc", "phase3_gate_portfolio"]
    btc = next(r for r in reports if r["name"] == "phase3_gate_btc")
    assert btc["symbol"] == "BTCUSDT"
    assert btc["overall_pass"] is True
    port = next(r for r in reports if r["name"] == "phase3_gate_portfolio")
    assert port["symbol"] == "ETHUSDT"  # first in the list


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
    # dangerous name. Belt-and-braces for any caller that bypasses the router.
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
