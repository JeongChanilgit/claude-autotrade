# Strategy N — Inside Bar Breakout

**FROZEN spec.**

## Thesis

이전 봉(mother bar) 범위 **안에 완전히 들어오는** inside bar는 변동성의 단기
압축을 의미한다. 압축이 해제되며 mother bar의 고점(저점)을 추세 방향으로 돌파하면
같은 방향의 추세 지속이 통계적으로 우세하다. EMA(trend) 필터로 역추세 진입을
걸러낸다.

## Signal Logic (deterministic, no look-ahead)

입력: `BarsData.ohlcv` (UTC DatetimeIndex).

1. `ema_trend = close.ewm(span=ema_trend_span, adjust=False).mean()`
2. Inside-bar 판정 (bar T): `high[T] < high[T-1]` AND `low[T] > low[T-1]`.
   - T-1을 mother bar, T를 inside bar로 부른다.
3. **롱 돌파** (bar T+1):
   - bar T에서 inside-bar가 성립했고,
   - `close[T+1] > high[T-1]` AND `close[T+1] > ema_trend[T+1]`.
4. **숏 돌파** (bar T+1):
   - bar T에서 inside-bar가 성립했고,
   - `close[T+1] < low[T-1]` AND `close[T+1] < ema_trend[T+1]`.
5. **청산**:
   - 롱: `close < ema_trend`.
   - 숏: `close > ema_trend`.
   - 또는 `holding_bars` 봉 경과.
6. 포지션은 한 번에 하나. 플랫 상태에서만 진입.
7. `size_hint = 1.0`, `stop = target = NaN`.

## 파라미터

| 이름 | 기본값 | 허용 범위 | 설명 |
|---|---|---|---|
| `ema_trend_span` | 50 | 5..500 | 추세 EMA 길이 |
| `holding_bars` | 12 | 1..200 | 최대 보유 봉 수 |

## Golden cases

모두 합성 15-분 봉, UTC DatetimeIndex, `symbol="TESTUSDT"`, `interval="15m"`.

### GC-N1: 롱 돌파 진입
EMA가 상승 중인 구간에서 inside bar가 형성되고 다음 봉이 mother bar의 high를
상향 돌파. **기대**: 돌파 봉에서 `side = +1`.

### GC-N2: EMA 하회 → flat
롱 진입 후 `close < ema_trend` 봉에서 `side = 0`.

### GC-N3: 멱등성
같은 입력 두 번 호출 시 SignalFrame 동일.

## 구현 제약

- `kairos.backtest.protocols.Strategy` 프로토콜 엄격 준수.
- inside-bar 판정은 **엄격 부등식** (`<`, `>`) — 동일 high/low 는 inside 아님.
- pandas → numpy 변환 후 정수 인덱싱.
- 구현 파일: `kairos/strategies/inside_bar_breakout.py`.
- 테스트 파일: `tests/unit/test_strategy_inside_bar_breakout.py`.
