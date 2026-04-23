# ADR-001 — Strategy D (Liquidation Fade) 폐기

**Status**: Accepted — 2026-04-23
**Decision-maker**: Opus (with user approval via `다 해` directive 2026-04-23)

## Context

Strategy D 원 설계는 Binance 공식 아카이브의 `liquidationSnapshot` 아카이브(`data/futures/um/{daily,monthly}/liquidationSnapshot/`)에 의존. 2026-04-23 S3 listing 확인 결과 해당 프리픽스가 **완전히 빔** — 비공식적으로 아카이브 제공이 중단됨.

## Options (ARCHITECTURE §2.2)

| # | 대안 | 평가 |
|---|---|---|
| a | aggTrades 기반 청산 시그니처 합성 | 디스크 ≈ 150GB (BTC/ETH/SOL × 5Y 월별 ZIP), 수십억 행 처리. 본 프로젝트 스코프 초과. |
| b | Binance `/fapi/v1/allForceOrders` 자동 수집 | 공식 API, **최근 7일 윈도우**만 제공. 5년 백필 불가 → OOS 검증 무의미. |
| c | 3rd-party 유료 (Coinalyze/CoinGlass 등) | **$0 원칙 위배**. |
| d | Strategy D 폐기, A/B/C 앙상블 재조정 | 전략 다양성 감소, 단 3개 무상관 전략으로 목표 지표 달성 가능 판단. |

## Decision — (d) 채택

### 앙상블 재조정

| 전략 | 기존 weight | 신규 weight | 이유 |
|---|---|---|---|
| A (ORB) | 30% | 40% | 상승추세·박스 레짐 모두에서 가장 안정적 기대. 일일 한계 손실이 bounded. |
| B (Funding Counter) | 25% | 30% | 레짐 독립적(펀딩 비대칭이 시황과 약한 상관). |
| C (Vol Compression) | 25% | 30% | 박스장 + 브레이크아웃 케이스에 특화. |
| D (Liquidation Fade) | 20% | **0% (제거)** | 데이터 부재. |

총합 100%. 상관 > 0.6 자동 축소 로직은 유지.

### 성공 기준 유지

원 목표 (CAGR ≥ 300%, MDD ≤ 35%, Sharpe ≥ 2.0) 그대로. 3개 전략 앙상블로 달성 불가 시 Phase 5 최종 Go/No-Go에서 프로젝트 종료 결정 — 이 또한 유효한 산출물.

## Consequences

- Phase 1 데이터 레이어: klines / funding / metrics 3종만 유지(이미 완료).
- Phase 3 Sonnet 플로틸라: 8개 → 6개 (3A/3B/3C 구현 + 3E/3F/3G 골든 테스트). 3D/3H 제거.
- `STATUS.md`의 "Strategy D 데이터 결정 보류" 섹션 제거.
- ARCHITECTURE §2.2의 경고 박스는 "폐기 결정됨, ADR-001 참조"로 축약.

## Revisit 조건

Binance가 `liquidationSnapshot` 아카이브를 재공개하거나, 무료 백필 가능한 대체 소스가 생기면 Strategy D 복구 검토. 본 ADR은 그 전까지 binding.
