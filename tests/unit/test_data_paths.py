from datetime import date

from kairos.data.paths import checksum_url, parquet_path, vision_zip_url


def test_klines_1m_path_and_url() -> None:
    p = parquet_path("klines_1m", "BTCUSDT", date(2024, 3, 15))
    assert p.as_posix().endswith(
        "data/klines/symbol=BTCUSDT/interval=1m/year=2024/month=03/BTCUSDT-1m-2024-03-15.parquet"
    )
    u = vision_zip_url("klines_1m", "BTCUSDT", date(2024, 3, 15))
    assert u == (
        "https://data.binance.vision/data/futures/um/daily/klines/"
        "BTCUSDT/1m/BTCUSDT-1m-2024-03-15.zip"
    )


def test_klines_15m_path_and_url() -> None:
    p = parquet_path("klines_15m", "ETHUSDT", date(2023, 12, 1))
    assert "interval=15m" in p.as_posix()
    assert "ETHUSDT-15m-2023-12-01.parquet" in p.name
    u = vision_zip_url("klines_15m", "ETHUSDT", date(2023, 12, 1))
    assert u.endswith("/15m/ETHUSDT-15m-2023-12-01.zip")


def test_funding_is_monthly() -> None:
    p = parquet_path("funding", "BTCUSDT", date(2024, 3, 15))  # day ignored
    assert p.as_posix().endswith(
        "data/funding/symbol=BTCUSDT/year=2024/month=03/BTCUSDT-funding-2024-03.parquet"
    )
    u = vision_zip_url("funding", "BTCUSDT", date(2024, 3, 15))
    assert u == (
        "https://data.binance.vision/data/futures/um/monthly/fundingRate/"
        "BTCUSDT/BTCUSDT-fundingRate-2024-03.zip"
    )


def test_metrics_is_daily() -> None:
    p = parquet_path("metrics", "SOLUSDT", date(2024, 6, 7))
    assert p.as_posix().endswith(
        "data/metrics/symbol=SOLUSDT/year=2024/month=06/SOLUSDT-metrics-2024-06-07.parquet"
    )
    u = vision_zip_url("metrics", "SOLUSDT", date(2024, 6, 7))
    assert u.endswith("/metrics/SOLUSDT/SOLUSDT-metrics-2024-06-07.zip")


def test_checksum_url_is_sibling() -> None:
    u = "https://data.binance.vision/path/file.zip"
    assert checksum_url(u) == u + ".CHECKSUM"
