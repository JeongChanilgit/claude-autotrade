"""Shared HTTP + checksum + ZIP utilities for all downloaders.

Sonnet implementations should call these helpers instead of reinventing retry logic.
"""
from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from kairos.data.protocols import ChecksumError

USER_AGENT = "kairos-backtest/0.1 (+github.com/local)"
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class TransientHTTPError(RuntimeError):
    """5xx or network timeout — safe to retry."""


class NotFoundError(RuntimeError):
    """404 — do not retry. Caller should log as gap and continue."""


@retry(
    retry=retry_if_exception_type(TransientHTTPError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
def http_get_bytes(url: str, client: httpx.Client | None = None) -> bytes:
    """GET a URL, return bytes. Raises NotFoundError on 404, TransientHTTPError on 5xx.

    The @retry decorator automatically retries TransientHTTPError up to 3x.
    """
    own_client = client is None
    c = client or httpx.Client(timeout=DEFAULT_TIMEOUT, headers={"User-Agent": USER_AGENT})
    try:
        r = c.get(url)
        if r.status_code == 404:
            raise NotFoundError(url)
        if r.status_code >= 500:
            raise TransientHTTPError(f"{r.status_code} {url}")
        r.raise_for_status()
        return r.content
    except httpx.TimeoutException as e:
        raise TransientHTTPError(f"timeout {url}") from e
    except httpx.TransportError as e:
        raise TransientHTTPError(f"transport {url}: {e}") from e
    finally:
        if own_client:
            c.close()


def verify_sha256(data: bytes, checksum_text: str, expected_filename: str) -> None:
    """Verify `data` matches the CHECKSUM sidecar text.

    Binance CHECKSUM format: "<64-hex-sha256>  <filename>" (two spaces).
    Raises ChecksumError on mismatch. `expected_filename` must appear in the line
    to defend against mis-labeled sidecars.
    """
    actual = hashlib.sha256(data).hexdigest()
    line = checksum_text.strip().splitlines()[0]
    parts = line.split()
    if len(parts) < 2:
        raise ChecksumError(f"malformed checksum line: {line!r}")
    expected_hash, fname = parts[0], parts[-1]
    if fname != expected_filename:
        raise ChecksumError(
            f"checksum sidecar filename mismatch: got {fname!r}, expected {expected_filename!r}"
        )
    if actual.lower() != expected_hash.lower():
        raise ChecksumError(
            f"sha256 mismatch for {expected_filename}: "
            f"expected={expected_hash}, actual={actual}"
        )


def open_single_csv_from_zip(zip_bytes: bytes) -> tuple[str, bytes]:
    """Extract the single .csv file from a Binance Vision ZIP archive.

    Returns (inner_filename, csv_bytes). Raises RuntimeError if the archive
    does not contain exactly one .csv entry.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        csv_entries = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if len(csv_entries) != 1:
            raise RuntimeError(
                f"expected exactly 1 CSV in archive, got {len(csv_entries)}: {csv_entries}"
            )
        inner = csv_entries[0]
        return inner, zf.read(inner)


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
