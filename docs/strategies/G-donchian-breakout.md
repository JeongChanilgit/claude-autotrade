# Strategy G — Donchian Channel Breakout

**FROZEN spec.**

## Thesis

15분봉에서 직전 N봉의 고점/저점으로 구성되는 Donchian 채널을 **close 기준으로
이탈**할 때 추세의 시작으로 해석하여 이탈 방향으로 진입한다. 반대 채널 이탈
또는 보유 상한 도달로 청산한다.

## Signal Logic (deterministic, no look-ahead)

입력: `BarsData.ohlcv` (UTC DatetimeIndex).

1. **채널 계산 (현재 봉 제외)**:
   - `upper[T] = max(high[T-N..T-1])` ⇨ `high.rolling(N).max().shift(1)`
   - `lower[T] = min(low[T-N..T-1])`  ⇨ `low.rolling(N).min().shift(1)`
2. **롱 진입 (플랫 시)**: `close[T] > upper[T]`.
3. **숏 진입 (플랫 시)**: `close[T] < lower[T]`.
4. **청산**:
   - 롱 보유 중 `close[T] < lower[T]` 또는 `holding_bars` 도달 → `side=0`.
   - 숏 보유 중 `close[T] > upper[T]` 또는 `holding_bars` 도달 → `side=0`.
5. 동시에 한 방향 포지션만 보유. 강제 청산된 봉에서는 같은 봉 재진입 금지.
6. `size_hint = 1.0`, `stop = target = NaN` (wrapper-level).

## 파라미터

| 이름 | 기본값 | 허용 범위 | 설명 |
|---|---|---|---|
| `channel` | 20 | 2..500 | Donchian 채널 길이 (봉) |
| `holding_bars` | 48 | 1..1000 | 최대 보유 봉 수 |

## Golden cases

모두 합성 15-분 봉, UTC DatetimeIndex, `symbol="TESTUSDT"`, `interval="15m"`.

### GC-G1: 롱 돌파 진입
N봉 고점 레인지 이후 급등하여 close 가 상단 채널 상향 돌파 → `side=+1`.

### GC-G2: 반대 채널 이탈 → flat
롱 진입 후 close 가 하단 채널 이탈 → `side=0`.

### GC-G3: 멱등성
같은 입력 두 번 호출 시 SignalFrame 동일.

## 구현 제약

- `kairos.backtest.protocols.Strategy` 프로토콜 엄격 준수.
- 현재 봉 자체를 채널에 포함시키지 않기 위해 `.rolling(N).max().shift(1)` 사용.
- 구현 파일: `kairos/strategies/donchian_breakout.py`.
- 테스트 파일: `tests/unit/test_strategy_donchian_breakout.py`.
