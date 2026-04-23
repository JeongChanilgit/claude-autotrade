# Kairos

OOS 백테스트 전용 자동매매 검증 시스템. 4개 전략(ORB / Funding Counter / Vol Compression / Liquidation Fade) 앙상블이 라이브 자본을 투입할 가치가 있는지 1차 판단하기 위한 로컬 PC 검증 툴.

**범위 밖**: 실시간 체결, 거래소 API, 주문 라우팅. 본 프로젝트는 데이터 수집 + 이중 엔진 백테스트 + 5중 검증 + 결과 대시보드까지.

## 성공 기준

| 항목 | 목표 |
|---|---|
| OOS CAGR | ≥ 300% |
| OOS MDD | ≤ 35% |
| OOS Sharpe | ≥ 2.0 |
| 엔진 간 PnL 일치도 (vectorbt vs backtrader) | ±1% |
| 레짐별 손익분기점 이상 | 4개 레짐 모두 |
| 테스트 커버리지 | ≥ 85% |

## 요구사항

- **Python 3.11 또는 3.12** (vectorbt 호환성 — 3.13/3.14는 미지원 가능)
- Node.js 20+ · npm
- Windows 10/11 네이티브 (bash 도구 사용 시 Git Bash 또는 유사 환경 권장)
- 50GB SSD 여유 (틱 포함 시 150GB)

## 설치

```bash
# 1. Python 가상환경 (3.11 또는 3.12)
py -3.11 -m venv .venv
.venv\Scripts\activate

# 2. Python + 웹 의존성 설치
python make.py install
```

## 실행

```bash
python make.py run           # FastAPI(8000) + Vite(5173) 동시 기동
python make.py run-api       # FastAPI만
python make.py run-web       # Vite만
python make.py test          # pytest + coverage
python make.py lint          # ruff + mypy
```

빌드한 SPA로 단일 포트 서빙하려면:
```bash
python make.py build-web
python make.py run-api
# → http://localhost:8000 에서 정적 서빙
```

## 디렉토리 구조

```
kairos/
  data/         # Binance Vision 다운로더 + DuckDB 쿼리
  backtest/     # vectorbt + backtrader 이중 엔진, 비용/지표/리스크/워크포워드
  strategies/   # A/B/C/D + 앙상블
  risk/         # 리스크 시뮬레이터 (일/주/월 한도, Kelly 1/4)
  dashboard/
    api/        # FastAPI 백엔드
    web/        # React SPA (Vite + Tailwind v4 + Recharts + Zustand)
tests/
  golden/       # 전략별 손계산 검증 (Opus 직접 작성)
  integration/  # 네트워크/파일시스템 실타
  unit/         # 순수 단위
docs/           # ARCHITECTURE / STRATEGIES / DATA
```

## 개발 플로우

- **Opus**: 인터페이스 계약 / 통합 / Go-No-Go 판정 / 전략 리뷰
- **Sonnet**: 명세 고정된 모듈 병렬 구현 (동시 최대 8개, git worktree 격리)
- **세션 인수인계**: 매 세션 끝에 `STATUS.md` 갱신

자세한 계획은 `docs/ARCHITECTURE.md` 및 플랜 파일 참고.

## 현재 상태

**Phase 0 — 환경 구축 완료.** 다음: Phase 1 (데이터 파이프라인, Opus가 Parquet 스키마 고정 후 Sonnet 4개 병렬).
