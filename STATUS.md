# STATUS

매 세션 종료 시 Opus가 갱신. 다음 세션에서 **이 파일부터 읽고 시작**.

---

## 현재 Phase

**Phase 1 완료** — 다운로더 4종 + DuckDB + 품질 리포트 + CLI. 게이트 통과. Phase 2 진입 대기 (vectorbt/backtrader 설치는 Python 3.11/3.12 필요).

## ⚠️ Strategy D 데이터 결정 보류 (2026-04-23 발견)

Binance Vision에서 **`liquidationSnapshot` 아카이브가 제공 중단됨** (`data/futures/um/{daily,monthly}/liquidationSnapshot/` 프리픽스 S3 listing empty). Strategy D 원 설계는 이 데이터에 의존.

Phase 1 스코프는 klines / funding / metrics 3종으로 축소. Strategy D용 대체 데이터는 **Phase 3 시작 시점에 사용자 확인 후 결정** — 후보는 ARCHITECTURE §2.2 참조 (aggTrades 합성 ≈ 150GB / API 수집 / 폐기 중 택1).

## 완료된 작업

**Phase 0**
- Git 저장소, `.gitignore`, 디렉토리 스켈레톤
- `pyproject.toml` (core / engines / reports / dev 분리 extras)
- `make.py` Windows 네이티브 러너
- FastAPI `/api/health` + React (Vite + Tailwind v4) scaffold
- `README.md`, `docs/ARCHITECTURE.md`, `STATUS.md`
- 커밋 `5c14e33 chore: phase 0 scaffold`
- 로컬 venv는 Python **3.13.5** (vectorbt 설치 Phase 2 시작에서 재검증 필요)

**Phase 1 Opus 선행**
- Binance Vision URL 패턴 4종 curl 검증 (klines/funding/metrics 정상, liquidationSnapshot 제거됨)
- 실제 CSV 샘플로 스키마 확정 (klines 12→13 cols, funding 3→4 cols, metrics 8 cols w/ 문자열 create_time)
- `kairos/data/protocols.py` — `Downloader` Protocol + `FetchResult` + 3개 pyarrow 스키마 + 파티션 키
- `kairos/data/paths.py` — `parquet_path()` / `vision_zip_url()` / `checksum_url()`
- `kairos/data/http.py` — `http_get_bytes` (tenacity retry, 404↔5xx 분리) + `verify_sha256` + `open_single_csv_from_zip`
- `kairos/data/cli.py` — `download` / `quality-report` 서브커맨드 스텁
- 단위 테스트 10건 (경로·URL·체크섬·ZIP 파서) 전부 녹색
- `docs/ARCHITECTURE.md` §2 전체 갱신 (확정 스키마 + DuckDB DDL + 다운로더 7규약)

## Phase 1 실적 (2026-04-23 완료)

- Sonnet-1A/1B/1C: klines(1m/15m) / funding / metrics 다운로더. worktree 미사용(greenfield git repo 초기화 전), 파일이 겹치지 않아 안전.
- Sonnet-1D: **rate limit으로 무출력** → Opus 직접 구현 (query.py / quality_report.py + 테스트 8건).
- 테스트 37개 전부 녹색 (단위). `tests/integration/test_download_*_live.py` 3개는 네트워크 필요해 기본 실행 제외.
- Opus가 수정한 버그: Sonnet-1C에서 `pq.read_table(target)` 사용 → Hive 파티션 경로에서 dict encoded `symbol` 컬럼 merge 실패. `pq.ParquetFile(target).read()`로 교체 (1A가 먼저 발견한 동일 이슈).
- Phase 1 게이트 통과: BTCUSDT 1m 2024-03-15~21 다운로드 → DuckDB 일봉 집계 → 인접 일자 open/close 연속성 완벽 (7일 연속 0.1 단위 일치).
- CLI: `python -m kairos.data.cli download --symbols BTCUSDT --start 2024-03-15 --end 2024-03-21 [--datasets ...]`, `quality-report --symbols ... --start ... --end ...`

## 다음에 할 것 (Phase 2 진입)

1. ~~Python 3.11/3.12 설치 필요~~ **해결**: vectorbt 1.0.0이 Python 3.13 cp313 휠 제공 (numba 0.65 / llvmlite 0.47). 3.13.5에 `vectorbt==1.0.0 + backtrader` 설치 완료, SMA 크로스오버 실 실행 확인, 기존 37 테스트 회귀 없음 (pandas 3.0.2 → 2.3.3 다운그레이드됨 — vectorbt 의존).
2. **Opus**: `kairos/backtest/protocols.py` 인터페이스 고정 (Strategy / Engine / BacktestResult / SignalFrame / CostModel / RiskRules).
3. **Sonnet 6개 병렬 출격** (Phase 2 본격 시작): 2A vbt 어댑터, 2B bt 어댑터, 2C 비용, 2D 지표, 2E 양자화, 2F walk-forward+OOS 게이트.

## 파라미터 튜닝 카운터 (전략당 10회 상한)

| 전략 | 조정 횟수 | 최근 조정 사유 |
|---|---|---|
| A (ORB) | 0 | — |
| B (Funding) | 0 | — |
| C (Vol Compression) | 0 | — |
| D (Liquidation Fade) | 0 | — |

## 열려있는 이슈 / 주의사항

- **Binance Vision URL 패턴**: 심볼별 파일 존재 여부 편차 있음(특히 과거 날짜). 404는 `skipped=True, rows=0`로 넘기는 다운로더 규약으로 처리 중.
- **pandas 2.3 다운그레이드**: vectorbt 1.0.0이 pandas < 3을 요구해 3.0.2 → 2.3.3. 기존 코드 회귀 없음 확인. 향후 pandas 3 복귀는 vectorbt 릴리스 대기.
