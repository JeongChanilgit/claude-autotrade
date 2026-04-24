"""Pydantic DTOs for the dashboard REST + WS API — Phase 5 portfolio shape.

Shapes mirror docs/phase5/API_V2.md verbatim. Single-symbol fields are gone;
every run is a portfolio keyed by symbol.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


IntervalName = Literal["1m", "15m"]
EngineName = Literal["vectorbt", "backtrader"]
RunStatus = Literal["pending", "running", "done", "failed"]
ExitReason = Literal["signal", "stop", "target", "eod_flatten", "risk_halt"]
ParamFieldType = Literal["int", "float", "bool", "string", "enum"]
ErrorCode = Literal[
    "RUN_NOT_FOUND",
    "VALIDATION_ERROR",
    "DATA_UNAVAILABLE",
    "ENGINE_ERROR",
    "INTERNAL_ERROR",
    "STRATEGY_NOT_FOUND",
]


class StrategySpec(BaseModel):
    """One entry in ``CreatePortfolioRunRequest.strategies`` (symbol -> spec)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    params: dict[str, Any] = Field(default_factory=dict)


class CreatePortfolioRunRequest(BaseModel):
    """POST /api/runs body — portfolio shape."""

    model_config = ConfigDict(extra="forbid")

    strategies: dict[str, StrategySpec]
    weights: dict[str, float]
    interval: IntervalName
    start: str
    end: str
    engine: EngineName
    initial_cash: float = 10_000.0
    apply_risk_simulator: bool = True

    @model_validator(mode="after")
    def _validate_portfolio(self) -> CreatePortfolioRunRequest:
        # At least one symbol is required — empty portfolios have no meaning.
        if not self.strategies:
            raise ValueError("at least one symbol is required")
        if set(self.strategies.keys()) != set(self.weights.keys()):
            raise ValueError(
                "strategies and weights must cover the same set of symbols"
            )
        total = sum(float(v) for v in self.weights.values())
        # Small epsilon tolerates float-rounding of user-supplied fractions.
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"weights must sum to 1.0 (got {total!r})")
        return self


class RunSummary(BaseModel):
    """Compact summary as returned by GET /api/runs."""

    id: str
    symbols: list[str]
    strategies: dict[str, str]
    weights: dict[str, float]
    interval: str
    start: str
    end: str
    engine: EngineName
    status: RunStatus
    created_at: str
    # Populated only when status == "done".
    cagr: float | None = None
    mdd: float | None = None
    sharpe: float | None = None
    total_trades: int | None = None


class RunDetail(RunSummary):
    """Full detail returned by GET /api/runs/{id}."""

    strategy_params: dict[str, dict[str, Any]] = Field(default_factory=dict)
    initial_cash: float = 10_000.0
    apply_risk_simulator: bool = True
    metrics: dict[str, float] | None = None
    per_symbol_metrics: dict[str, dict[str, float]] | None = None
    error: str | None = None


class Trade(BaseModel):
    """A single closed trade row — leading ``symbol`` column is new in v2."""

    symbol: str
    entry_time: str
    exit_time: str
    side: Literal[1, -1]
    qty: float
    entry_price: float
    exit_price: float
    gross_pnl: float
    fees: float
    slippage: float
    funding_cost: float
    net_pnl: float
    bars_held: int
    exit_reason: ExitReason


class ParamField(BaseModel):
    """Describes one configurable parameter of a strategy."""

    model_config = ConfigDict(extra="forbid")

    type: ParamFieldType
    default: Any = None
    min: float | None = None
    max: float | None = None
    choices: list[str] | None = None
    help: str = ""


class StrategyEntry(BaseModel):
    """One entry returned by GET /api/strategies."""

    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    default_params: dict[str, Any] = Field(default_factory=dict)
    param_schema: dict[str, ParamField] = Field(default_factory=dict)
    supports_funding: bool = False
    supports_metrics: bool = True
    description: str = ""


# --- EquityPayload is a plain dict on the wire (shape varies by per_symbol). --


class EquityPayloadTotal(BaseModel):
    """Portfolio-total shape — returned when per_symbol=False."""

    timestamps: list[str]
    equity: list[float]


class EquityPayloadPerSymbol(BaseModel):
    """Per-symbol shape — returned when per_symbol=True."""

    timestamps: list[str]
    per_symbol: dict[str, list[float]]


# Union type for callers that want static typing on the payload shape.
EquityPayload = EquityPayloadTotal | EquityPayloadPerSymbol


class MetricDiff(BaseModel):
    a: float
    b: float
    rel_diff: float


class EquityDiff(BaseModel):
    timestamps: list[str]
    a: list[float]
    b: list[float]


class CompareReport(BaseModel):
    a: RunSummary
    b: RunSummary
    metric_diff: dict[str, MetricDiff]
    equity_diff: EquityDiff


class GateReportSummary(BaseModel):
    name: str
    symbol: str
    interval: str
    start: str
    end: str
    overall_pass: bool


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str


class ErrorBody(BaseModel):
    error: ErrorDetail


__all__ = [
    "CompareReport",
    "CreatePortfolioRunRequest",
    "EngineName",
    "EquityDiff",
    "EquityPayload",
    "EquityPayloadPerSymbol",
    "EquityPayloadTotal",
    "ErrorBody",
    "ErrorCode",
    "ErrorDetail",
    "ExitReason",
    "GateReportSummary",
    "IntervalName",
    "MetricDiff",
    "ParamField",
    "ParamFieldType",
    "RunDetail",
    "RunStatus",
    "RunSummary",
    "StrategyEntry",
    "StrategySpec",
    "Trade",
]
