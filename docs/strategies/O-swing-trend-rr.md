# Strategy O — Swing Trend Pullback (R:R)

**FROZEN spec.**

## Thesis

EMA 추세 필터(단기 EMA > 장기 EMA = 상승추세)에서 **직전 봉이 단기 EMA 아래로
눌렸다가 현재 봉에서 다시 탈환**하는 흐름을 눌림목 되돌림의 종료로 해석해
추세 방향으로 진입한다. 손절은 최근 스윙 저가/고가로 **고정**, 익절은 손익비
배수로 자동 산출한다. 전략 내부에서 stop/target 터치를 스캔해 청산한다.

## Signal Logic (deterministic, no look-ahead)

입력: `BarsData.ohlcv` (UTC DatetimeIndex).

1. **지표**:
   - `ema_fast = close.ewm(span=ema_fast, adjust=False).mean()`
   - `ema_slow = close.ewm(span=ema_slow, adjust=False).mean()`
   - `swing_low  = low.rolling(swing_window).min()`
   - `swing_high = high.rolling(swing_window).max()`
2. **롱 진입 (플랫 시)**:
   - `ema_fast[T] > ema_slow[T]` (상승추세)
   - `close[T-1] < ema_fast[T-1]` 및 `close[T] > ema_fast[T]` (눌림→탈환)
   - `stop = swing_low[T]`. `stop < close[T]` 가 아니면 진입 건너뜀.
   - `target = close[T] + risk_reward × (close[T] − stop)`
3. **숏 진입 (플랫 시)**: 대칭 (`ema_fast < ema_slow`, `swing_high` 사용).
4. **청산 (T+1 이후 봉마다 스캔)**:
   - 롱: `low[t] ≤ stop` → stop 청산; 아니면 `high[t] ≥ target` → target 청산.
   - 숏: 대칭.
   - 같은 봉에서 stop/target 동시 터치 시 **stop 우선**.
   - `holding_bars` 도달 시 해당 봉 close로 강제 청산.
5. 청산 봉부터 `side=0`, `stop=target=NaN`.
6. `size_hint = 1.0`.

## 파라미터

| 이름 | 기본값 | 허용 범위 | 설명 |
|---|---|---|---|
| `ema_fast` | 20 | 2..200 | 단기 EMA 길이 |
| `ema_slow` | 50 | 5..500 | 장기 EMA 길이 |
| `swing_window` | 10 | 2..100 | 손절용 스윙 룩백 봉 수 |
| `risk_reward` | 2.5 | 0.5..10.0 | 손절 대비 익절 배수(손익비) |
| `holding_bars` | 60 | 1..500 | 최대 보유 봉 수 |

## Golden cases

모두 합성 15-분 봉, UTC DatetimeIndex, `symbol="TESTUSDT"`, `interval="15m"`.

### GC-O1: 눌림 탈환 롱 + target 청산
상승추세 중 눌림 후 탈환 → `side[T]=+1`. 직후 봉의 high가 target 터치 →
해당 봉 `side=0`.

### GC-O2: 눌림 탈환 롱 + stop 청산
동일 진입 후 직후 봉의 low가 스윙 저가(stop)를 깨면 해당 봉 `side=0`.

### GC-O3: 멱등성
같은 입력 두 번 호출 시 SignalFrame 동일.

## 구현 제약

- `kairos.backtest.protocols.Strategy` 프로토콜 엄격 준수.
- 구현 파일: `kairos/strategies/swing_trend_rr.py`.
- 테스트 파일: `tests/unit/test_strategy_swing_trend_rr.py`.
