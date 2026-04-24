# Strategy H — RSI(2) Pullback

**FROZEN spec.**

## Thesis

Connors-style 단기 평균회귀. 장기 추세(`close vs SMA(trend_sma_window)`)의
방향과 같은 쪽에서 RSI(2) 극단치로 이탈한 봉에서 진입해 RSI 가 정상 구간으로
되돌아올 때 청산한다.

## Signal Logic (deterministic, no look-ahead)

입력: `BarsData.ohlcv` (UTC DatetimeIndex).

1. `rsi2 = wilder_rsi(close, rsi_window)` — Wilder 스무딩(α=1/n).
2. `trend_sma = close.rolling(trend_sma_window).mean()`.
3. **롱 진입 (플랫 시)**: `close > trend_sma` AND `rsi2 < rsi_long_thresh`.
4. **숏 진입 (플랫 시)**: `close < trend_sma` AND `rsi2 > rsi_short_thresh`.
5. **청산**:
   - 롱 보유 중 `rsi2 > 50` 또는 `close < trend_sma` 또는 `holding_bars` 도달 → `side=0`.
   - 숏 보유 중 `rsi2 < 50` 또는 `close > trend_sma` 또는 `holding_bars` 도달 → `side=0`.
6. 동시에 한 방향 포지션만 보유. 강제 청산된 봉에서는 같은 봉 재진입 금지.
7. `size_hint = 1.0`, `stop = target = NaN` (wrapper-level).

## 파라미터

| 이름 | 기본값 | 허용 범위 | 설명 |
|---|---|---|---|
| `rsi_window` | 2 | 2..50 | Wilder RSI 길이 (극단용 짧게) |
| `trend_sma_window` | 200 | 10..2000 | 장기 추세 SMA 길이 |
| `rsi_long_thresh` | 10.0 | 1..40 | 롱 진입 RSI 임계 |
| `rsi_short_thresh` | 90.0 | 60..99 | 숏 진입 RSI 임계 |
| `holding_bars` | 12 | 1..200 | 최대 보유 봉 수 |

## Golden cases

모두 합성 15-분 봉, UTC DatetimeIndex, `symbol="TESTUSDT"`, `interval="15m"`.

### GC-H1: 롱 진입 (상승 추세 + RSI(2) 과매도)
SMA 상단에서의 단기 급락으로 rsi2 < rsi_long_thresh → `side=+1`.

### GC-H2: RSI 복귀 → flat
롱 진입 후 rsi2 가 50 위로 복귀 → `side=0`.

### GC-H3: 멱등성
같은 입력 두 번 호출 시 SignalFrame 동일.

## 구현 제약

- `kairos.backtest.protocols.Strategy` 프로토콜 엄격 준수.
- RSI 는 Wilder 스무딩(α=1/n), `pandas.ewm(alpha=1/n, adjust=False)`.
- 구현 파일: `kairos/strategies/rsi2_pullback.py`.
- 테스트 파일: `tests/unit/test_strategy_rsi2_pullback.py`.
