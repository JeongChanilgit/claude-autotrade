# Strategy Q — Pivot Target Reversion (R:R)

**FROZEN spec.**

## Thesis

전일 OHLC로 산출한 **데일리 플로어-트레이더 피봇** S1/R1 터치 + close 되돌림을
역추세 진입 신호로 사용한다. 익절은 피봇 **P 복귀(고정 target)**, 손절은
target 거리를 손익비로 나눠 자동 산출. 전략 내부에서 stop/target 터치로 청산.

## Signal Logic (deterministic, no look-ahead)

입력: `BarsData.ohlcv` (UTC DatetimeIndex).

1. **데일리 피봇 (UTC day D의 값은 D-1 OHLC로 산출)**:
   - `daily = ohlcv.resample('1D').agg({'high':'max','low':'min','close':'last'})`
   - `.shift(1)` → 각 봉은 **전일** OHLC 기준 P/S1/R1 사용 (look-ahead 방지).
   - `P = (H + L + C) / 3`, `S1 = 2P - H`, `R1 = 2P - L`.
2. **롱 진입 (플랫 시)**: `low[T] ≤ S1[T] < close[T]` (S1 터치 후 되돌림).
   추가로 `P[T] > close[T]`여야 한다(P가 아래면 스킵).
   - `target = P[T]`, `D = |target - close[T]|`, `stop = close[T] - D / risk_reward`.
3. **숏 진입 (플랫 시)**: `high[T] ≥ R1[T] > close[T]` 및 `P[T] < close[T]` (대칭).
4. **청산 (T+1 이후 봉마다 스캔)**:
   - 롱: `low[t] ≤ stop` → stop 청산. 아니면 `high[t] ≥ target` → target 청산.
   - 숏: 대칭.
   - 한 봉에서 stop/target 동시 터치 시 **stop 우선**.
   - UTC day 경계 교차 시 강제 청산 (피봇이 재설정되므로).
   - `holding_bars` 도달 시 강제 청산.
5. 청산 봉부터 `side=0`, `stop=target=NaN`.
6. `size_hint = 1.0`.

## 파라미터

| 이름 | 기본값 | 허용 범위 | 설명 |
|---|---|---|---|
| `risk_reward` | 2.0 | 0.5..10.0 | 고정 익절 대비 손절 배수(손익비) |
| `holding_bars` | 48 | 1..500 | 최대 보유 봉 수 |

## Golden cases

모두 합성 15-분 봉, UTC DatetimeIndex, `symbol="TESTUSDT"`, `interval="15m"`.
전일 OHLC가 필요하므로 **최소 2 UTC일** 분량을 생성한다.

### GC-Q1: 롱 진입 → target 청산
Day 1 H=110, L=90, C=100 → Day 2 P=100, S1=90, R1=110.
Day 2의 한 봉에서 `low=89.5`, `close=95` → `side=+1`, target=100, stop=92.5.
다음 봉 high가 100 돌파 → `side=0`.

### GC-Q2: 롱 진입 → stop 청산
동일 진입. 다음 봉 low가 92.5 이하로 하락 → `side=0`.

### GC-Q3: 멱등성
같은 입력 두 번 호출 시 SignalFrame 동일.

## 구현 제약

- `kairos.backtest.protocols.Strategy` 프로토콜 엄격 준수.
- 구현 파일: `kairos/strategies/pivot_target_rr.py`.
- 테스트 파일: `tests/unit/test_strategy_pivot_target_rr.py`.
