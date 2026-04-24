# Strategy D — VWAP Reversion

**FROZEN spec.**

## Thesis

15분봉 intraday 세션에서 가격이 **VWAP으로부터 N-σ 이상 이탈**하면 단기 평균회귀가
통계적으로 우세하다. 세션은 UTC 기준 00:00-23:59 하루 단위로 리셋하고, VWAP / σ 모두
**당일 시작 시점부터 누적**한다. RSI(Wilder, n) 극단치로 과매수/과매도를 추가 확인한다.

## Signal Logic (deterministic, no look-ahead)

입력: `BarsData.ohlcv` (UTC DatetimeIndex).

1. 각 UTC 일자별로 **세션 누적 VWAP**과 **가격-VWAP 편차의 누적 표준편차 σ**를 계산.
   - `typical = (high + low + close) / 3`
   - `VWAP_t = cum(typical * volume) / cum(volume)` (당일 첫 봉부터 t까지)
   - `σ_t = sqrt( cum((typical − VWAP)² * volume) / cum(volume) )`
2. RSI(Wilder, `rsi_window`)을 전체 시계열에 대해 계산 (세션 초과 이월 허용 —
   과거 봉만 참조하므로 look-ahead 없음).
3. **롱 진입**: `close < VWAP − sigma_mult * σ` AND `RSI < rsi_oversold`.
4. **숏 진입**: `close > VWAP + sigma_mult * σ` AND `RSI > rsi_overbought`.
5. **청산**:
   - 포지션 보유 중 `close` 가 VWAP을 역방향으로 터치(롱이면 `close >= VWAP`,
     숏이면 `close <= VWAP`) → 해당 봉에서 `side=0`.
   - 또는 `holding_bars` 봉 경과 → 다음 봉부터 `side=0` (즉 보유 상한 도달 봉까지 유지).
   - 또는 당일 마지막 봉(session_end) → 세션 종료와 함께 flatten.
6. 같은 UTC 일자 내에서 재진입 허용 (다른 봉에서 진입 조건 재성립 시).
7. `size_hint = 1.0`, `stop = target = NaN` (wrapper-level).

## 파라미터

| 이름 | 기본값 | 허용 범위 | 설명 |
|---|---|---|---|
| `sigma_mult` | 2.0 | 0.5..5.0 | VWAP 편차 σ 배수 임계 |
| `rsi_window` | 14 | 2..100 | Wilder RSI 길이 |
| `rsi_oversold` | 30.0 | 5..45 | 롱 진입 RSI 임계 |
| `rsi_overbought` | 70.0 | 55..95 | 숏 진입 RSI 임계 |
| `holding_bars` | 12 | 1..200 | 최대 보유 봉 수 |
| `min_session_bars` | 4 | 1..50 | σ 계산 최소 누적 봉 수 |

## Golden cases

모두 합성 15-분 봉, UTC DatetimeIndex, `symbol="TESTUSDT"`, `interval="15m"`.

### GC-D1: 롱 진입 (VWAP 하향 이탈 + RSI 과매도)
당일 누적 VWAP 계산 후 특정 봉에서 close < VWAP − 2σ, RSI < 30. **기대**: 해당 봉에서 `side=+1`.

### GC-D2: VWAP 복귀 → flat
GC-D1 이후 close가 VWAP을 상향 돌파하는 봉에서 `side=0`.

### GC-D3: 멱등성
같은 입력 두 번 호출 시 SignalFrame 동일.

## 구현 제약

- `kairos.backtest.protocols.Strategy` 프로토콜 엄격 준수.
- 세션 경계는 UTC 날짜(`index.normalize()`)로 구분, `.groupby`로 누적 계산 (`.groupby().cumsum()`).
- RSI는 Wilder 스무딩(α=1/n), `pandas.ewm(alpha=1/n, adjust=False)`.
- 구현 파일: `kairos/strategies/vwap_reversion.py`.
- 테스트 파일: `tests/unit/test_strategy_vwap_reversion.py`.
