"""PDF report export for the Kairos dashboard — Phase 5 portfolio shape.

Exposes ``reports_router`` with ``GET /api/runs/{id}/report.pdf`` — renders a
small one-or-two-page PDF summarising a portfolio run's metrics and equity curve.

The PDF build is intentionally minimal:
  * matplotlib renders the equity curve to a PNG buffer (Agg backend, no GUI).
  * reportlab stitches a cover page + symbols/weights table + metrics table +
    the PNG into the doc.

No side effects on disk — everything is buffered in memory and streamed back.
"""
from __future__ import annotations

import io
from typing import Any

import matplotlib

# Headless server context — must be set before pyplot import.
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from fastapi import APIRouter  # noqa: E402
from fastapi.responses import Response  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import letter  # noqa: E402
from reportlab.lib.styles import getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import inch  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from kairos.dashboard.api import repo  # noqa: E402
from kairos.dashboard.api.errors import KairosAPIError  # noqa: E402

reports_router = APIRouter(prefix="/api")


# Ordered list of metric keys surfaced in the PDF table — same set the
# frontend RunResults page shows so the exported doc stays consistent.
_METRIC_ROWS: list[tuple[str, str, str]] = [
    ("cagr", "CAGR", "pct"),
    ("mdd", "MDD", "pct"),
    ("sharpe", "Sharpe", "num3"),
    ("calmar", "Calmar", "num3"),
    ("profit_factor", "Profit Factor", "num3"),
    ("win_rate", "Win %", "pct"),
    ("total_trades", "Total Trades", "int"),
]


def _fmt_metric(val: Any, kind: str) -> str:
    if val is None:
        return "-"
    try:
        f = float(val)
    except (TypeError, ValueError):
        return "-"
    if f != f:  # NaN check without importing math
        return "-"
    if kind == "pct":
        return f"{f * 100:.2f}%"
    if kind == "int":
        return str(int(round(f)))
    return f"{f:.3f}"


def _equity_png(timestamps: list[str], equity: list[float]) -> bytes:
    """Render an equity curve to a PNG byte buffer. Empty inputs yield a stub."""
    fig, ax = plt.subplots(figsize=(7.0, 3.2), dpi=110)
    if equity:
        ax.plot(range(len(equity)), equity, color="#10b981", linewidth=1.2)
        ax.set_xlabel("Bar index")
        ax.set_ylabel("Equity")
        ax.grid(True, alpha=0.25)
        # Tick labels default to dense integers on a long series — sparse is fine.
        if len(timestamps) >= 2:
            ax.set_title(f"{timestamps[0]}  ->  {timestamps[-1]}", fontsize=9)
    else:
        ax.text(0.5, 0.5, "No equity data", ha="center", va="center")
        ax.set_axis_off()
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def _attr_or_key(obj: Any, name: str, default: Any = None) -> Any:
    # Repo returns Pydantic models; tests may hand in plain dicts. Accept both.
    if hasattr(obj, name):
        return getattr(obj, name)
    if isinstance(obj, dict):
        return obj.get(name, default)
    return default


def _extract_equity(payload: Any) -> tuple[list[str], list[float]]:
    """Normalise repo.load_equity return into (timestamps, equity) tuples."""
    ts = _attr_or_key(payload, "timestamps", None) or []
    eq = _attr_or_key(payload, "equity", None) or []
    return list(ts), [float(x) for x in eq]


def _build_pdf(detail: Any, timestamps: list[str], equity: list[float]) -> bytes:
    """Render the report PDF into an in-memory bytes buffer."""
    styles = getSampleStyleSheet()
    out = io.BytesIO()
    doc = SimpleDocTemplate(
        out,
        pagesize=letter,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.7 * inch,
        title=f"Kairos backtest {getattr(detail, 'id', '')}",
    )

    story: list[Any] = []
    story.append(Paragraph("Kairos Backtest Report", styles["Title"]))
    story.append(Spacer(1, 0.15 * inch))

    symbols = getattr(detail, "symbols", None) or []
    # `strategies` on RunSummary/RunDetail is dict[symbol, strategy_name].
    strategies_map = getattr(detail, "strategies", None) or {}
    if isinstance(strategies_map, dict):
        strategy_names = sorted({str(v) for v in strategies_map.values()})
    else:
        strategy_names = []
    cover_rows = [
        ["Run ID", str(getattr(detail, "id", ""))],
        ["Strategies", ", ".join(strategy_names) if strategy_names else "-"],
        ["Symbols", ", ".join(str(s) for s in symbols) if symbols else "-"],
        ["Interval", str(getattr(detail, "interval", ""))],
        ["Window",
         f"{getattr(detail, 'start', '')}  ->  {getattr(detail, 'end', '')}"],
        ["Engine", str(getattr(detail, "engine", ""))],
        ["Status", str(getattr(detail, "status", ""))],
    ]
    cover = Table(cover_rows, colWidths=[1.6 * inch, 5.0 * inch])
    cover.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), "Helvetica", 10),
                ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 10),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(cover)
    story.append(Spacer(1, 0.2 * inch))

    # Portfolio breakdown: symbol -> weight. Sorted for deterministic output.
    weights = getattr(detail, "weights", None) or {}
    if isinstance(weights, dict) and weights:
        story.append(Paragraph("Portfolio Weights", styles["Heading2"]))
        weight_rows: list[list[str]] = [["Symbol", "Weight"]]
        for sym in sorted(weights.keys()):
            try:
                pct = f"{float(weights[sym]) * 100:.2f}%"
            except (TypeError, ValueError):
                pct = "-"
            weight_rows.append([str(sym), pct])
        wtab = Table(weight_rows, colWidths=[2.8 * inch, 2.0 * inch])
        wtab.setStyle(
            TableStyle(
                [
                    ("FONT", (0, 0), (-1, -1), "Helvetica", 10),
                    ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 10),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                    ("ALIGN", (1, 1), (1, -1), "RIGHT"),
                ]
            )
        )
        story.append(wtab)
        story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph("Metrics", styles["Heading2"]))
    metrics = getattr(detail, "metrics", None) or {}
    metric_table_rows: list[list[str]] = [["Metric", "Value"]]
    for key, label, kind in _METRIC_ROWS:
        metric_table_rows.append([label, _fmt_metric(metrics.get(key), kind)])
    mtab = Table(metric_table_rows, colWidths=[2.8 * inch, 2.0 * inch])
    mtab.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), "Helvetica", 10),
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 10),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("ALIGN", (1, 1), (1, -1), "RIGHT"),
            ]
        )
    )
    story.append(mtab)
    story.append(Spacer(1, 0.25 * inch))

    story.append(Paragraph("Equity Curve", styles["Heading2"]))
    png_bytes = _equity_png(timestamps, equity)
    story.append(Image(io.BytesIO(png_bytes), width=6.6 * inch, height=3.0 * inch))

    doc.build(story)
    return out.getvalue()


@reports_router.get("/runs/{run_id}/report.pdf")
def get_run_report_pdf(run_id: str) -> Response:
    detail = repo.get_run(run_id)
    if detail is None:
        raise KairosAPIError("RUN_NOT_FOUND", f"run {run_id!r} not found", status=404)

    # Equity may be absent for still-running or failed runs — render a stub
    # rather than 404'ing the whole PDF; metadata alone is still useful.
    try:
        payload = repo.load_equity(run_id, per_symbol=False)
        ts, eq = _extract_equity(payload)
    except Exception:
        ts, eq = [], []

    pdf_bytes = _build_pdf(detail, ts, eq)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{run_id}.pdf"',
        },
    )


__all__ = ["reports_router"]
