# ARCHITECTURE

인터페이스 계약 누적 문서. **구현 전에 여기 먼저 박아넣는다**. Sonnet 위임 프롬프트에 관련 섹션을 그대로 첨부.

---

## 1. 전체 구조

```
Browser (5173 dev / 8000 prod)
  │
  ▼
FastAPI (kairos.dashboard.api)
  │   ├── REST /api/*
  │   └── WebSocket /ws/backtest/{id}
  ▼
Backtest Core (kairos.backtest)
  │   Engine-A (vectorbt)  ──┐
  │   Engine-B (backtrader) ─┴── Strategy + Costs + Risk + Metrics
  ▼
Storage
  ├── Parquet (원자료, Binance Vision 다운로드분)
  ├── DuckDB  (분석 쿼리 뷰)
  └── SQLite  (백테스트 실행 이력 메타)
```

---

## 2. 데이터 레이어 (Phase 1에서 확정 예정)

> 🟡 **TODO (Phase 1 선행 작업)**: Parquet 스키마·파티셔닝·DuckDB 뷰·`Downloader` 프로토콜을 여기 확정한 뒤 Sonnet 위임.

```
data/
  ohlcv/  symbol=BTCUSDT/year=2024/month=03/interval=1m/part-0.parquet
  funding/ symbol=BTCUSDT/year=2024/month=03/part-0.parquet
  liquidations/ symbol=BTCUSDT/year=2024/month=03/part-0.parquet
```

OHLCV 컬럼 (안): `open_time (int64 ms UTC) | open (float64) | high | low | close | volume | symbol (string) | interval (string)`

---

## 3. 백테스트 프로토콜 (Phase 2 선행 작업에서 확정)

> 🟡 **TODO (Phase 2 선행 작업)**: `kairos/backtest/protocols.py`에 아래 프로토콜을 구현한 뒤 Sonnet 위임.

드래프트:

```python
class Strategy(Protocol):
    name: str
    def generate_signals(self, data: BarsData, params: dict) -> SignalFrame: ...

class Engine(Protocol):
    def run(self, strategy, data, params, costs, risk) -> BacktestResult: ...

@dataclass
class BacktestResult:
    equity_curve: pd.Series
    trades: pd.DataFrame
    metrics: dict
    engine_name: str
```

**규칙**:
- 리스크 시뮬레이션(일/주/월 한도, EOD flatten, Kelly 1/4)은 **엔진 위 래퍼**에서만. 각 엔진이 자체 구현하면 교차검증 의미 없음.
- 수량/가격 양자화는 `kairos.backtest.quantize` 한 곳에서만 (Decimal 기반).
- OOS 6개월은 `kairos.data.oos_gate`에서 물리적으로 차단.

---

## 4. API 스펙 (Phase 4 선행 작업에서 확정)

> 🟡 **TODO (Phase 4 선행 작업)**: OpenAPI 스펙 파일로 분리 작성.

드래프트:
- `GET  /api/health` — 생존 확인 (Phase 0 완료)
- `GET  /api/runs` — 실행 이력 리스트
- `POST /api/runs` — 새 백테스트 시작
- `GET  /api/runs/{id}` — 실행 상세
- `GET  /api/runs/{id}/trades` — 거래 리스트 (페이지네이션)
- `POST /api/compare` — 다중 실행 비교
- `WS   /ws/backtest/{id}` — 진행률 스트림 `{progress, stage, log}`

---

## 5. 프론트 라우팅 (Phase 4)

- `/` Home — 실행 목록
- `/run` Run — 백테스트 시작 폼
- `/runs/:id` Results — 상세 결과
- `/compare` Compare — 최대 6개 오버레이
- `/trades` Trades — 전역 거래 검색

---

## 6. 레짐 정의

Phase 3에서 전략을 평가할 4분할 레짐:

| 레짐 | 시기 예시 | 정의 |
|---|---|---|
| 상승추세 | 2020-10 ~ 2021-04 / 2023-10 ~ 2024-03 | 일봉 종가가 SMA200 위 + 30일 수익률 > +20% |
| 박스 | 2023-04 ~ 2023-09 | 30일 수익률 ∣·∣ < 10% + ATR/가격 < 중앙값 |
| 급락 | LUNA 2022-05 / FTX 2022-11 / 2024-08 엔캐리 | 주간 수익률 < -15% |
| 저변동 | 2023-06 ~ 2023-08 | 실현변동성 20일 <연율 40% |

정확한 구간·임계값은 Phase 3에서 Opus가 고정.

---

## 7. 세션 운영

- Sonnet 위임 전: 이 문서의 관련 섹션 + `protocols.py` + 골든 케이스를 프롬프트에 첨부
- Sonnet 산출물은 worktree 격리 후 Opus가 직접 리뷰 → 머지
- 리뷰 체크리스트: lookahead bias / 라이브러리 환각 / Decimal 누락 / 테스트 존재 / 인터페이스 준수
