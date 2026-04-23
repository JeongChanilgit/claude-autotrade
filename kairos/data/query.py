"""DuckDB query layer. Thin wrappers that expose parquet partitions as SQL views."""
from __future__ import annotations

from pathlib import Path

import duckdb
import pyarrow as pa

from kairos.data import paths
from kairos.data.protocols import (
    SCHEMA_FUNDING,
    SCHEMA_KLINES,
    SCHEMA_METRICS,
    Dataset,
)

_DATASET_DIR: dict[Dataset, str] = {
    "klines_1m": "klines",
    "klines_15m": "klines",
    "funding": "funding",
    "metrics": "metrics",
}

_VIEW_FILTER: dict[str, tuple[Dataset, str | None]] = {
    "v_klines_1m": ("klines_1m", "1m"),
    "v_klines_15m": ("klines_15m", "15m"),
    "v_funding": ("funding", None),
    "v_metrics": ("metrics", None),
}


_PA_TO_DUCKDB: dict[str, str] = {
    "int64": "BIGINT",
    "int32": "INTEGER",
    "double": "DOUBLE",
    "string": "VARCHAR",
}


def _duckdb_type(field: pa.Field) -> str:
    try:
        return _PA_TO_DUCKDB[str(field.type)]
    except KeyError as exc:
        raise RuntimeError(f"no DuckDB mapping for pyarrow type {field.type}") from exc


def _empty_view_sql(view: str, schema: pa.Schema) -> str:
    cols = ", ".join(f"NULL::{_duckdb_type(f)} AS {f.name}" for f in schema)
    return f"CREATE OR REPLACE VIEW {view} AS SELECT {cols} WHERE 1 = 0"


def _schema_for(view: str) -> pa.Schema:
    dataset, _ = _VIEW_FILTER[view]
    if dataset in ("klines_1m", "klines_15m"):
        return SCHEMA_KLINES
    if dataset == "funding":
        return SCHEMA_FUNDING
    return SCHEMA_METRICS


def _glob_has_parquet(data_root: Path, dataset: Dataset) -> bool:
    top = data_root / _DATASET_DIR[dataset]
    if not top.exists():
        return False
    return next(top.rglob("*.parquet"), None) is not None


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    del read_only  # in-memory DB, flag kept for API symmetry
    return duckdb.connect(":memory:")


def create_views(
    con: duckdb.DuckDBPyConnection,
    data_root: Path | None = None,
) -> None:
    root = data_root if data_root is not None else paths.DATA_ROOT
    for view, (dataset, interval_filter) in _VIEW_FILTER.items():
        if not _glob_has_parquet(root, dataset):
            con.execute(_empty_view_sql(view, _schema_for(view)))
            continue
        glob = (root / _DATASET_DIR[dataset] / "**" / "*.parquet").as_posix()
        if interval_filter is not None:
            sql = (
                f"CREATE OR REPLACE VIEW {view} AS "
                f"SELECT * FROM read_parquet('{glob}', hive_partitioning=1) "
                f"WHERE interval = '{interval_filter}'"
            )
        else:
            sql = (
                f"CREATE OR REPLACE VIEW {view} AS "
                f"SELECT * FROM read_parquet('{glob}', hive_partitioning=1)"
            )
        con.execute(sql)


def views_present(con: duckdb.DuckDBPyConnection) -> set[str]:
    rows = con.execute(
        "SELECT view_name FROM duckdb_views() WHERE NOT internal"
    ).fetchall()
    return {r[0] for r in rows}
