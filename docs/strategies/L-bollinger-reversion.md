# Strategy L — Bollinger %B Reversion

**FROZEN spec.**

## Thesis

15분봉에서 가격이 **Bollinger Band**을 벗어나 과도하게 늘어진 뒤, 다음 봉이 밴드
안으로 되돌아오면 평균회귀 진입 기회가 형성된다. Bollinger %B (Percent-B)는 가격이
하단·상단 밴드 사이 어디쯤 있는지를 0..1 범위로 표현하며, %B < 0 또는 %B > 1은
밴드 이탈을 의미한다. 직전 봉에서 이탈이 확인되고 현재 봉 종가가 다시 밴드 안으로
복귀했을 때 평균(중단 SMA) 복귀를 목표로 진입한다.

## Signal Logic (deterministic, no look-ahead)

입력: `BarsData.ohlcv` (UTC DatetimeIndex).

1. 지표 계산:
   - `mid = close.rolling(bb_window).mean()`
   - `std = close.rolling(bb_window).std(ddof=0)`
   - `upper = mid + bb_std * std`
   - `lower = mid - bb_std * std`
   - `pct_b = (close - lower) / (upper - lower)` (분모 0 이면 NaN)
2. **롱 진입**: 직전 봉 `pct_b[T-1] < 0` AND 현재 봉 `close[T] > lower[T]`
   (밴드 하단을 벗어났다가 복귀).
3. **숏 진입**: 직전 봉 `pct_b[T-1] > 1` AND 현재 봉 `close[T] < upper[T]`
   (밴드 상단을 벗어났다가 복귀).
4. **청산**:
   - 롱: `close >= mid` 이거나 `holding_bars` 봉 경과.
   - 숏: `close <= mid` 이거나 `holding_bars` 봉 경과.
5. 포지션은 한 번에 하나. 플랫 상태에서만 진입.
6. `size_hint = 1.0`, `stop = target = NaN`.

## 파라미터

| 이름 | 기본값 | 허용 범위 | 설명 |
|---|---|---|---|
| `bb_window` | 20 | 2..500 | BB 창 길이 |
| `bb_std` | 2.0 | 0.1..10.0 | BB 표준편차 배수 |
| `holding_bars` | 12 | 1..200 | 최대 보유 봉 수 |

## Golden cases

모두 합성 15-분 봉, UTC DatetimeIndex, `symbol="TESTUSDT"`, `interval="15m"`.

### GC-L1: 롱 진입 (하단 이탈 후 복귀)
안정 구간 뒤 급락 한 봉에서 `pct_b < 0`, 이어지는 봉에서 종가가 `lower` 위로 회복.
**기대**: 복귀 봉에서 `side = +1`.

### GC-L2: 중단 복귀 → flat
롱 진입 후 `close`가 `mid` 이상으로 복귀하는 봉에서 `side = 0`.

### GC-L3: 멱등성
같은 입력 두 번 호출 시 SignalFrame 동일.

## 구현 제약

- `kairos.backtest.protocols.Strategy` 프로토콜 엄격 준수.
- 분모 0(upper == lower)인 봉은 `pct_b` NaN 처리 → 진입 불가.
- 구현 파일: `kairos/strategies/bollinger_reversion.py`.
- 테스트 파일: `tests/unit/test_strategy_bollinger_reversion.py`.
