# STATUS

매 세션 종료 시 Opus가 갱신. 다음 세션에서 **이 파일부터 읽고 시작**.

---

## 현재 Phase

**Phase 3 구현 단계 완료** — 전략 A/B/C + 앙상블 + 레짐·스트레스 평가 하니스 + Phase 3 게이트 composer 전부 코드·테스트 완료. 실데이터 기반 게이트 실행은 downloader 대량 다운로드 후(현재 사용자 미실행).

## Strategy D 폐기 확정 (2026-04-23, ADR-001)

`liquidationSnapshot` 아카이브 제공 중단됨. 대안 (a) 디스크 150GB / (b) API 7일 백필 불가 / (c) $0 위배 → **(d) Strategy D 폐기, 앙상블 A40/B30/C30 재조정** 채택. 상세: `docs/ADR-001-strategy-d-dropped.md`.

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

## Phase 2 실적 (2026-04-23 완료)

- Opus 선행: `kairos/backtest/protocols.py` FROZEN (BarsData/SignalFrame/CostModel/RiskRules/SymbolSpec/BacktestResult/Strategy/Engine + 6개 불변).
- Sonnet 웨이브 1(3건 병렬): 2C costs(9 tests), 2D metrics(10 tests), 2E quantize(10 tests + `_symbol_specs.json`).
- Sonnet 웨이브 2(3건 병렬): 2A `engine_vbt.py`(8 tests, `price=open.shift(-1)`로 vbt 1.0 `"nextopen"` 문자열 미지원 우회), 2B `engine_bt.py`(8 tests, 브로커 기본 슬리피지 + 초과분 후처리, 수동 equity 기록), 2F `oos_gate.py`+`walkforward.py`(25 tests, 반개구간 엄격 overlap).
- Opus 웨이브 2 리뷰 수정: engine_bt의 `exit_reason` dtype을 `object`→`string`으로 수정(프로토콜 일치).
- Phase 2 Go/No-Go 게이트: `tests/unit/test_cross_validation.py` — SMA(10/30) 교차 800바 × vbt/bt 양쪽 실행 → 무비용·4.5 bps 모두 최종 equity 1% 이내 일치, 거래수 ±2 이내.
- Opus 직구현: `risk_simulator.py` — Kelly 사이징 + EOD flatten + 일/주/월 loss limit + MDD halt. 신호 재작성 + 엔진 재실행으로 최대 3패스 수렴. 9 tests.
- 전체 테스트 119개 녹색(단위).

## Phase 3 실적 (2026-04-23 완료)

- Opus 선행 골든 스펙: `docs/strategies/{A-orb, B-funding-counter, C-vol-compression, E-ensemble}.md` FROZEN. 각 전략당 5개 golden case + 파라미터 기본·허용범위 고정.
- Sonnet 3A/3B/3C 병렬 구현: `kairos/strategies/{orb,funding,vol_compression}.py` 각각 Strategy 프로토콜 엄격 준수, look-ahead 없음, `size_hint=1.0` 고정. 5 golden + 멱등 테스트 각 strategy.
- Opus 직구현 — 앙상블: `kairos/strategies/ensemble.py` (A40/B30/C30 가중합 + rolling pairwise correlation 기반 shrink). 9 tests.
- Opus 직구현 — 레짐 하니스: `kairos/backtest/regimes.py`(7 window) + `regime_evaluator.py` (slice_bars / evaluate_regimes / 가족별 break-even 게이트). 15 tests.
- Opus 직구현 — 스트레스 러너: `kairos/backtest/stress.py` (RiskSimulator 래핑 LUNA/FTX/yen-carry 3-crash 실행, 일간 -3% 한도 준수 검증). 4 tests.
- Opus 직구현 — Phase 3 게이트 composer: `kairos/backtest/phase3_gate.py` (4개 서브게이트 통합 리포트, 실패 원인 라벨링). 7 tests.
- 전체 테스트 181개 녹색.

## 다음에 할 것 (Phase 3 실데이터 실행 + Phase 4 준비)

1. **사용자 실행 필요**: `python -m kairos.data.cli download` 로 BTC/ETH/SOL 2020~2025 전체 다운로드 (로컬에서 오래 걸림). 실데이터 없으면 게이트가 합성 데이터로만 돌기 때문에 실제 판정 불가.
2. **실데이터 Phase 3 게이트 실행**: 다운로드 완료 후 Opus 가 각 전략 단독 walk-forward → Sharpe 산출, 앙상블 CAGR/MDD, 레짐별 break-even, 스트레스 3개 검증 → `compose_phase3_report()` 로 최종 판정.
3. **Phase 4 진입 조건**: 게이트 통과 시 대시보드(FastAPI + React) 병렬 플로틸라 출격. 실패 시 파라미터 튜닝(전략당 10회 상한) 또는 프로젝트 축소 검토.

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
