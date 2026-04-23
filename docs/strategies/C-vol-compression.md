# Strategy C — Volatility Compression Breakout

**FROZEN spec** — Sonnet-3C implements to this contract verbatim.

## Thesis

Bollinger Band(BB) 폭이 Keltner Channel(KC) 폭 안으로 좁아지는 "스퀴즈" 상태는
역사적으로 임박한 브레이크아웃의 선행 지표. 스퀴즈가 해제될 때 방향을 RSI
다이버전스로 결정한다.

## Signal Logic (deterministic, no look-ahead)

입력: `BarsData.ohlcv` (funding/metrics 불필요).

1. **Bollinger Band** (lookback=`bb_window`, stddev=`bb_std`)
   - `mid = SMA(close, bb_window)`
   - `upper_bb = mid + bb_std * STD(close, bb_window)`
   - `lower_bb = mid − bb_std * STD(close, bb_window)`
2. **Keltner Channel** (lookback=`kc_window`, atr_mult=`kc_mult`)
   - `ema_mid = EMA(close, kc_window)`
   - `atr = ATR(high, low, close, kc_window)` (Wilder, 봉 단위)
   - `upper_kc = ema_mid + kc_mult * atr`
   - `lower_kc = ema_mid − kc_mult * atr`
3. **스퀴즈 상태**: `upper_bb < upper_kc AND lower_bb > lower_kc`
   (BB가 KC 안에 완전히 포함).
4. **RSI**(lookback=`rsi_window`)로 다이버전스 확인:
   - 가격 신저가 갱신 + RSI 신저가 **미**갱신 (bullish divergence) → 롱 편향
   - 가격 신고가 갱신 + RSI 신고가 **미**갱신 (bearish divergence) → 숏 편향
   - "신고/신저"는 `divergence_lookback` 봉 범위 내.
5. **진입**:
   - 이전 봉이 스퀴즈 상태였고 현재 봉이 스퀴즈 **해제** (BB가 KC 밖으로 나옴) +
     직전 `divergence_lookback` 내 divergence 방향과 **일치**하는 방향으로 close가
     mid 돌파 → 그 방향으로 진입.
   - divergence가 없으면 신호 0.
6. **청산**:
   - 진입 후 `close` 가 `mid`를 반대 방향으로 통과 → flatten.
   - 또는 `holding_bars` 경과 → flatten.
7. `size_hint=1.0`, `stop=NaN`, `target=NaN`.

## 파라미터

| 이름 | 기본값 | 허용 범위 | 설명 |
|---|---|---|---|
| `bb_window` | 20 | 10..50 | BB/KC 공통 lookback |
| `bb_std` | 2.0 | 1.5..3.0 | BB 표준편차 배수 |
| `kc_window` | 20 | 10..50 | Keltner lookback (EMA/ATR) |
| `kc_mult` | 1.5 | 1.0..2.5 | KC ATR 배수 |
| `rsi_window` | 14 | 7..30 | RSI 기간 |
| `divergence_lookback` | 20 | 10..60 | divergence 판정 룩백 |
| `holding_bars` | 240 | 60..720 | 최대 보유 봉 |

## Golden cases

### GC-C1: 스퀴즈 → 해제 + bullish divergence → 롱 진입
- 바 [0..50] 가격 왕복이 점점 좁아짐 → 바 [30..50] 스퀴즈 활성.
- 바 40에서 가격 신저가, RSI는 신저가 미갱신 (bullish divergence).
- 바 51에서 스퀴즈 해제 + 가격이 mid 상향 돌파.
- **기대**: `side[51]=+1`.

### GC-C2: 스퀴즈 해제지만 divergence 없음 → 신호 0
- 스퀴즈 해제 발생, 근데 최근 divergence_lookback 안에 divergence 없음.
- **기대**: `side=0` 유지.

### GC-C3: 스퀴즈 → 해제 + bearish divergence → 숏 진입
- bar 40에서 가격 신고가, RSI 신고가 미갱신.
- bar 51 스퀴즈 해제 + 가격이 mid 하향 돌파.
- **기대**: `side[51]=-1`.

### GC-C4: mid 반대 방향 통과 → flatten
- GC-C1 세팅 후 bar 80에서 close < mid.
- **기대**: `side[80]=0`.

### GC-C5: holding_bars 도달 → flatten
- GC-C1 세팅 후 `holding_bars=240` 경과.
- **기대**: 241번째 봉부터 `side=0`.

## 구현 제약

- `kairos.backtest.protocols.Strategy` 엄격 준수.
- ATR은 봉 단위 Wilder (일봉 변환 금지 — 1m/15m 그대로).
- RSI는 Wilder smoothing (`alpha = 1/n`).
- 구현: `kairos/strategies/vol_compression.py`, 테스트: `tests/unit/test_strategy_vol_compression.py`.
- Golden case 5건 + 멱등성 테스트 1건.
