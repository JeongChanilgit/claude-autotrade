# Strategy F — EMA Pullback Trend

**FROZEN spec.**

## Thesis

15분봉 추세추종. 빠른/중간/느린 EMA 가 방향성 있게 정렬되어 있는 동안
가격이 **빠른 EMA로 되돌림(pullback)** 후 이탈 방향으로 재돌파하는 지점에
진입한다. 정렬이 깨지거나 최대 보유 봉 수를 초과하면 청산한다.

## Signal Logic (deterministic, no look-ahead)

입력: `BarsData.ohlcv` (UTC DatetimeIndex).

1. `ema_fast = close.ewm(span=f, adjust=False).mean()`; `ema_mid`, `ema_slow` 동일.
2. **롱 추세 정렬**: `ema_fast > ema_mid > ema_slow`.
   **숏 추세 정렬**: `ema_fast < ema_mid < ema_slow`.
3. **롱 진입 (봉 T)**: 롱 추세 정렬 AND `low[T-1] <= ema_fast[T-1]` AND `close[T] > ema_fast[T]`.
4. **숏 진입 (봉 T)**: 숏 추세 정렬 AND `high[T-1] >= ema_fast[T-1]` AND `close[T] < ema_fast[T]`.
5. **청산**:
   - 추세 정렬이 깨지면(롱이면 not long-trend, 숏이면 not short-trend) 해당 봉에서 `side=0`.
   - 보유 `holding_bars` 도달 시 다음 봉에서 `side=0` (held==holding_bars 인 봉까지 유지).
   - 강제 청산된 봉에서는 같은 봉 재진입 금지.
6. 동시에 한 방향 포지션만 보유.
7. `size_hint = 1.0`, `stop = target = NaN` (wrapper-level).

## 파라미터

| 이름 | 기본값 | 허용 범위 | 설명 |
|---|---|---|---|
| `ema_fast` | 20 | 2..200 | 빠른 EMA 길이 |
| `ema_mid` | 50 | 2..500 | 중간 EMA 길이 |
| `ema_slow` | 200 | 2..1000 | 느린 EMA 길이 |
| `holding_bars` | 32 | 1..500 | 최대 보유 봉 수 |

## Golden cases

모두 합성 15-분 봉, UTC DatetimeIndex, `symbol="TESTUSDT"`, `interval="15m"`.

### GC-F1: 롱 풀백 진입
장기 상승 추세(정렬 `fast > mid > slow`) 중 특정 봉에서 low가 ema_fast 를 터치한 뒤
다음 봉에서 close 가 ema_fast 위로 재돌파. **기대**: 해당 봉에서 `side=+1`.

### GC-F2: 추세 붕괴 → flat
롱 진입 후 가격 급락으로 `ema_fast > ema_mid > ema_slow` 정렬이 무너지는 봉에서 `side=0`.

### GC-F3: 멱등성
같은 입력 두 번 호출 시 SignalFrame 동일.

## 구현 제약

- `kairos.backtest.protocols.Strategy` 프로토콜 엄격 준수.
- EMA 는 `pandas.ewm(span=n, adjust=False).mean()` 사용 (정규 EMA).
- look-ahead 방지: pullback 조건은 바 T-1, 재돌파 조건은 바 T 값으로 판정.
- 구현 파일: `kairos/strategies/ema_pullback.py`.
- 테스트 파일: `tests/unit/test_strategy_ema_pullback.py`.
