import hashlib
import io
import zipfile

import pytest

from kairos.data.http import open_single_csv_from_zip, verify_sha256
from kairos.data.protocols import ChecksumError


def _make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_open_single_csv_from_zip_happy_path() -> None:
    payload = b"a,b\n1,2\n"
    zbytes = _make_zip({"inner.csv": payload})
    name, got = open_single_csv_from_zip(zbytes)
    assert name == "inner.csv"
    assert got == payload


def test_open_single_csv_rejects_multiple_csv() -> None:
    zbytes = _make_zip({"a.csv": b"x", "b.csv": b"y"})
    with pytest.raises(RuntimeError, match="exactly 1 CSV"):
        open_single_csv_from_zip(zbytes)


def test_verify_sha256_matches() -> None:
    data = b"hello world"
    h = hashlib.sha256(data).hexdigest()
    verify_sha256(data, f"{h}  myfile.zip\n", "myfile.zip")


def test_verify_sha256_mismatch() -> None:
    with pytest.raises(ChecksumError, match="sha256 mismatch"):
        verify_sha256(b"abc", "0" * 64 + "  myfile.zip", "myfile.zip")


def test_verify_sha256_wrong_filename() -> None:
    data = b"hello"
    h = hashlib.sha256(data).hexdigest()
    with pytest.raises(ChecksumError, match="sidecar filename mismatch"):
        verify_sha256(data, f"{h}  other.zip", "myfile.zip")
