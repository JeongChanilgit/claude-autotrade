# Strategy J — Liquidity Sweep Reversal

**FROZEN spec.**

## Thesis

15분봉 intraday 구간에서 직전 N봉 고점/저점은 stop 클러스터가 쌓이는 지점이다.
한 봉의 극단이 이 경계를 돌파했지만 해당 봉 **close**가 다시 안쪽으로 돌아오면
**실패한 브레이크아웃(= 스톱 헌트)** 으로 해석하고 반대 방향으로 fade한다.

## Signal Logic (deterministic, no look-ahead)

입력: `BarsData.ohlcv` (UTC DatetimeIndex).

1. `prior_high = high.rolling(lookback).max().shift(1)` — 이전 `lookback`봉 최고가.
2. `prior_low = low.rolling(lookback).min().shift(1)` — 이전 `lookback`봉 최저가.
3. **숏 진입** (bull trap, bar T):
   - `high[T] > prior_high[T]` AND `close[T] < prior_high[T]` AND
     `(high[T] − close[T]) / close[T] >= min_wick_pct`.
4. **롱 진입** (bear trap, bar T):
   - `low[T] < prior_low[T]` AND `close[T] > prior_low[T]` AND
     `(close[T] − low[T]) / close[T] >= min_wick_pct`.
5. **청산**:
   - `holding_bars` 보유 상한 도달 → 다음 봉 `side=0`.
   - 또는 반대 방향 유효 sweep 신호 발생 → 해당 봉 flatten (same-bar 재진입 없음).
6. 동시에 하나의 포지션만 유지. flat 상태에서만 진입 판정.
7. `size_hint = 1.0`, `stop = target = NaN`.

## 파라미터

| 이름 | 기본값 | 허용 범위 | 설명 |
|---|---|---|---|
| `lookback` | 20 | 2..500 | 이전 고가/저가 집계 봉 수 |
| `min_wick_pct` | 0.002 | 0.0..0.1 | 최소 꼬리 비율 (close 대비) |
| `holding_bars` | 8 | 1..200 | 최대 보유 봉 수 |

## Golden cases

모두 합성 15-분 봉, UTC DatetimeIndex, `symbol="TESTUSDT"`, `interval="15m"`.

### GC-J1: 숏 진입 (bull trap sweep)
평평한 범위 뒤 한 봉이 직전 20봉 고점을 돌파했다가 close가 다시 안쪽으로
되돌아옴. **기대**: 해당 봉에서 `side=-1`.

### GC-J2: 보유 상한 청산
GC-J1 진입 후 `holding_bars` 봉 보유, 직후 봉에서 `side=0`.

### GC-J3: 멱등성
같은 입력 두 번 호출 시 SignalFrame 동일.

## 구현 제약

- `kairos.backtest.protocols.Strategy` 프로토콜 엄격 준수.
- 극단 롤링은 `pandas.Series.rolling(n).max()/min()` 사용, `shift(1)`로 당봉 제외.
- 반대 방향 신호에 의한 청산은 동일 봉에서 새 포지션을 재오픈하지 않는다.
- 구현 파일: `kairos/strategies/liquidity_sweep.py`.
- 테스트 파일: `tests/unit/test_strategy_liquidity_sweep.py`.
