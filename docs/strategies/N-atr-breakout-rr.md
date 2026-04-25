# Strategy N — ATR Breakout (R:R)

**FROZEN spec.**

## Thesis

직전 N봉 Donchian 고/저가를 **close 기준 돌파**할 때 추세의 시작으로 해석해
돌파 방향 진입한다. 손절은 진입가 대비 **ATR 배수** 고정, 익절은 **손익비
(risk:reward) 배수**로 자동 산출한다. 엔진/래퍼 개입 없이 전략 내부에서
고가/저가 터치를 기준으로 stop/target 청산을 수행한다.

## Signal Logic (deterministic, no look-ahead)

입력: `BarsData.ohlcv` (UTC DatetimeIndex).

1. **채널/변동성 계산 (현재 봉 제외)**:
   - `upper[T] = high.rolling(lookback).max().shift(1)`
   - `lower[T] = low.rolling(lookback).min().shift(1)`
   - Wilder ATR: `TR.ewm(alpha=1/atr_window, adjust=False).mean()`.
2. **롱 진입 (플랫 시)**: `close[T] > upper[T]`.
   - `stop = close[T] − stop_atr_mult × ATR[T]`
   - `target = close[T] + risk_reward × (close[T] − stop)`
3. **숏 진입 (플랫 시)**: `close[T] < lower[T]` (대칭).
4. **청산 (T+1 이후 봉마다 스캔)**:
   - 롱: `low[t] ≤ stop` → stop 청산(같은 봉에서 `side=0`).
          아니면 `high[t] ≥ target` → target 청산.
   - 숏: 대칭.
   - 한 봉에서 stop/target 동시 터치 시 **stop 우선** (보수적 가정).
   - `holding_bars` 도달 시 해당 봉 close로 강제 청산.
5. 청산 봉부터 `side=0`, `stop=target=NaN`. 보유 중 봉은 진입 시 산출한 stop/
   target 값을 그대로 유지한다.
6. `size_hint = 1.0` (엔진/래퍼가 사이징을 처리한다).

## 파라미터

| 이름 | 기본값 | 허용 범위 | 설명 |
|---|---|---|---|
| `lookback` | 20 | 5..200 | Donchian 룩백 봉 수 |
| `atr_window` | 14 | 2..100 | ATR 길이 |
| `stop_atr_mult` | 1.5 | 0.2..5.0 | 진입가 대비 손절 ATR 배수 |
| `risk_reward` | 2.0 | 0.5..10.0 | 손절 대비 익절 배수(손익비) |
| `holding_bars` | 48 | 1..500 | 최대 보유 봉 수 |

## Golden cases

모두 합성 15-분 봉, UTC DatetimeIndex, `symbol="TESTUSDT"`, `interval="15m"`.

### GC-N1: 롱 돌파 + target 청산
플랫 레인지 이후 상단 돌파 → `side[T]=+1`. 직후 봉의 high가 target 터치 →
해당 봉에서 `side=0`.

### GC-N2: 롱 돌파 + stop 청산
동일 진입 조건. 직후 봉의 low가 stop을 깨면 해당 봉에서 `side=0`.

### GC-N3: 멱등성
같은 입력 두 번 호출 시 SignalFrame 동일.

## 구현 제약

- `kairos.backtest.protocols.Strategy` 프로토콜 엄격 준수.
- 구현 파일: `kairos/strategies/atr_breakout_rr.py`.
- 테스트 파일: `tests/unit/test_strategy_atr_breakout_rr.py`.
