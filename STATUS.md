# STATUS

매 세션 종료 시 Opus가 갱신. 다음 세션에서 **이 파일부터 읽고 시작**.

---

## 현재 Phase

**Phase 1 — 데이터 파이프라인** (Opus 선행 작업 완료, Sonnet 4개 병렬 출격 직전)

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

## 다음에 할 것 (순서)

1. **Opus**: 이 상태 커밋 후 Sonnet 4개 worktree 병렬 출격
   - Sonnet-1A `kairos/data/download_klines.py` (1m + 15m, 일별 ZIP → Parquet, 증분 갱신)
   - Sonnet-1B `kairos/data/download_funding.py` (monthly ZIP → Parquet, 8h 간격 정합성)
   - Sonnet-1C `kairos/data/download_metrics.py` (daily ZIP → Parquet, `create_time` ms 파싱)
   - Sonnet-1D `kairos/data/query.py` + `kairos/data/quality_report.py` (DuckDB 뷰 DDL, 갭/결측/이상치 리포트, CLI 연결)
2. **Opus 리뷰 게이트**: 4개 완료 후 일괄 리뷰 (interface 위반 / lookahead / 테스트 누락 체크)
3. **Opus**: BTC 단일 월 전체 다운로드 → DuckDB 뷰로 일봉 집계 → Binance 공식 가격 ±0.01 확인 (Phase 1 게이트)
4. **Phase 2 진입**: vectorbt / backtrader 설치 (Python 3.11/3.12 미설치면 이 시점에 해결)

## 파라미터 튜닝 카운터 (전략당 10회 상한)

| 전략 | 조정 횟수 | 최근 조정 사유 |
|---|---|---|
| A (ORB) | 0 | — |
| B (Funding) | 0 | — |
| C (Vol Compression) | 0 | — |
| D (Liquidation Fade) | 0 | — |

## 열려있는 이슈 / 주의사항

- **Python 버전**: 시스템에 3.11/3.12 없음. 사용자 설치 전까지 `make install` 실행 불가. pyproject 제약으로 venv 생성 시점부터 차단.
- **Binance Vision URL 패턴**: Phase 1 시작 전 공식 문서로 최신 경로 확인 필요. 심볼별 파일 존재 여부 편차 있음(특히 과거 날짜).
- **vectorbt 라이선스/Python 호환**: OSS `vectorbt` 버전을 사용. PyPI 최신 호환 버전 재확인 후 pin.
