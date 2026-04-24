# Strategy I — MACD Zero-Cross Momentum

**FROZEN spec.**

## Thesis

15분봉에서 MACD 라인(빠른 EMA − 느린 EMA)이 0선을 상향/하향 돌파하는 순간은
모멘텀 전환의 통계적 신호다. 저변동성 구간(횡보)에서의 잦은 가짜 신호를 배제하기
위해 Wilder ATR 비율 하한으로 필터링한다.

## Signal Logic (deterministic, no look-ahead)

입력: `BarsData.ohlcv` (UTC DatetimeIndex).

1. `ema_fast = close.ewm(span=fast, adjust=False).mean()`, `ema_slow` 동일.
2. `macd = ema_fast − ema_slow`.
3. `signal = macd.ewm(span=signal_span, adjust=False).mean()` — 기록 목적.
4. `atr = wilder_atr(high, low, close, atr_window)` (EWM α=1/n).
5. **롱 진입** (bar T): `macd[T-1] <= 0` AND `macd[T] > 0` AND `atr[T]/close[T] >= atr_min_pct`.
6. **숏 진입** (bar T): `macd[T-1] >= 0` AND `macd[T] < 0` AND ATR 필터 동일.
7. **청산**:
   - 보유 중 반대 방향 0선 교차 (롱 중 `macd[T] < 0`, 숏 중 `macd[T] > 0`) → 해당 봉 flatten.
   - 또는 `holding_bars` 만큼 보유 → 마지막 유지 봉은 `entry_bar + holding_bars − 1`,
     다음 봉 `side=0`.
8. 동시에 하나의 포지션만 유지. flat 상태에서만 진입 판정.
9. `size_hint = 1.0`, `stop = target = NaN`.

## 파라미터

| 이름 | 기본값 | 허용 범위 | 설명 |
|---|---|---|---|
| `fast` | 12 | 2..100 | MACD 빠른 EMA |
| `slow` | 26 | 2..500 | MACD 느린 EMA |
| `signal_span` | 9 | 2..200 | MACD 시그널 EMA |
| `atr_window` | 14 | 2..200 | Wilder ATR 길이 |
| `atr_min_pct` | 0.003 | 0.0..0.1 | 최소 ATR/close (변동성 필터) |
| `holding_bars` | 24 | 1..500 | 최대 보유 봉 수 |

## Golden cases

모두 합성 15-분 봉, UTC DatetimeIndex, `symbol="TESTUSDT"`, `interval="15m"`.

### GC-I1: 롱 진입 (MACD 상향 0선 돌파)
하락 추세로 MACD를 음수 영역에 위치시킨 뒤, 급반등으로 0선을 상향 돌파.
**기대**: 돌파 봉에서 `side=+1`.

### GC-I2: 반대 방향 0선 교차 청산
GC-I1 진입 후 반락으로 MACD가 0선을 하향 돌파하는 봉에서 `side=0`.

### GC-I3: 멱등성
같은 입력 두 번 호출 시 SignalFrame 동일.

## 구현 제약

- `kairos.backtest.protocols.Strategy` 프로토콜 엄격 준수.
- EMA는 `pandas.ewm(span=n, adjust=False)` 재귀 초깃값(첫 값 = 첫 close) 사용.
- ATR은 Wilder α=1/n EWM, True Range = max(h−l, |h−prev_close|, |l−prev_close|).
- 구현 파일: `kairos/strategies/macd_zero_cross.py`.
- 테스트 파일: `tests/unit/test_strategy_macd_zero_cross.py`.
