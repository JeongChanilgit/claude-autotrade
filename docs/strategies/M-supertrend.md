# Strategy M — Supertrend Follower

**FROZEN spec.**

## Thesis

ATR 기반 Supertrend 라인은 트렌드 전환점에서 상단↔하단을 뒤집으며, 뒤집힌 방향을
유지하는 구간에서 추세 추종 포지션을 단순하게 유지할 수 있다. 15분봉의 잡음에도
Wilder ATR 평활과 상·하단 밴드의 "더 타이트한 쪽으로만 이동" 규칙이 불필요한
방향 전환을 억제한다.

## Signal Logic (deterministic, no look-ahead)

입력: `BarsData.ohlcv` (UTC DatetimeIndex).

1. `atr = wilder_atr(high, low, close, atr_window)` (Wilder 평활).
2. `hl2 = (high + low) / 2`
3. `upper_basic = hl2 + atr_mult * atr`
4. `lower_basic = hl2 - atr_mult * atr`
5. 스무딩된 밴드 (반복):
   - `upper_band[t] = upper_basic[t] if upper_basic[t] < upper_band[t-1] or close[t-1] > upper_band[t-1] else upper_band[t-1]`
   - `lower_band[t] = lower_basic[t] if lower_basic[t] > lower_band[t-1] or close[t-1] < lower_band[t-1] else lower_band[t-1]`
6. 방향:
   - `direction[t-1] == +1` 이고 `close[t] < lower_band[t]` → `-1`로 flip.
   - `direction[t-1] == -1` 이고 `close[t] > upper_band[t]` → `+1`로 flip.
   - 그 외 유지.
7. ATR 안정 전(초기 `atr_window` 봉)엔 `direction = 0`, `side = 0`.
8. `side[t] = direction[t]` (flip 반영). 반대 방향 flip 시 자동 청산.
9. `holding_bars` 봉 경과 시 강제 flat (`side=0`), 방향은 초기화.
10. `size_hint = 1.0`, `stop = target = NaN`.

## 파라미터

| 이름 | 기본값 | 허용 범위 | 설명 |
|---|---|---|---|
| `atr_window` | 10 | 2..200 | Wilder ATR 길이 |
| `atr_mult` | 3.0 | 0.5..10.0 | ATR 배수 |
| `holding_bars` | 96 | 1..1000 | 최대 보유 봉 수 (cap) |

## Golden cases

모두 합성 15-분 봉, UTC DatetimeIndex, `symbol="TESTUSDT"`, `interval="15m"`.

### GC-M1: 상승 추세 진입
초기 조용한 구간 뒤 뚜렷한 상승 랠리가 시작되는 봉에서 `direction`이 `+1`로 flip
→ 그 이후 봉에서 `side = +1` 유지.

### GC-M2: 반대 flip 시 청산
상승 추세에서 가격이 `lower_band` 아래로 종가 돌파하는 봉에서 `direction = -1` flip
→ `side = -1` (또는 이후 동일 값 유지).

### GC-M3: 멱등성
같은 입력 두 번 호출 시 SignalFrame 동일.

## 구현 제약

- 상·하단 밴드와 방향은 **반복 계산** (state carry-over). 벡터화 금지.
- pandas → numpy 로 변환해 정수 인덱싱(FutureWarning 회피).
- 구현 파일: `kairos/strategies/supertrend.py`.
- 테스트 파일: `tests/unit/test_strategy_supertrend.py`.
