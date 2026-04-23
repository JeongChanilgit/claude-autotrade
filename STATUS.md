# STATUS

매 세션 종료 시 Opus가 갱신. 다음 세션에서 **이 파일부터 읽고 시작**.

---

## 현재 Phase

**Phase 0 — 환경 구축** (진행 중 / 체크포인트 검증 대기)

## 완료된 작업

- Git 저장소 초기화 (`main` 브랜치, LF 줄바꿈 강제)
- `.gitignore` (Python / Node / 데이터 제외)
- 디렉토리 스켈레톤: `kairos/{data,backtest,strategies,risk,dashboard/{api,web}}`, `tests/{golden,integration,unit}`, `docs`, `reports`, `artifacts`
- `pyproject.toml` — Python 3.11~3.12, 모든 주요 의존성 고정
- `make.py` — 크로스플랫폼 Python 러너 (install/test/lint/run/download-data/oos-validate/clean)
- `kairos/dashboard/api/main.py` — FastAPI `/api/health` + React dist 정적 서빙
- React 스캐폴드 (Vite 5 + React 18 + TS + Tailwind v4 + Recharts + Zustand)
- `README.md`, `docs/ARCHITECTURE.md`

## 다음에 할 것 (순서)

1. **사용자**: Python 3.11 또는 3.12 설치 (현재 시스템에 3.13/3.14만 있음, vectorbt 호환 불가)
2. **사용자**: `py -3.11 -m venv .venv && python make.py install`
3. **Opus**: `python make.py test` 녹색 확인 → Phase 0 체크포인트 통과
4. **Opus**: 첫 커밋 (`chore: phase 0 scaffold`)
5. **Opus** → Phase 1 시작: Parquet 스키마 / 파티셔닝 / DuckDB 뷰 / `Downloader` 프로토콜 설계. `docs/ARCHITECTURE.md` 데이터 섹션에 기록.
6. **Sonnet 4개 병렬 출격** (worktree 격리): 1A OHLCV, 1B 펀딩, 1C 청산, 1D DuckDB+품질리포트

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
