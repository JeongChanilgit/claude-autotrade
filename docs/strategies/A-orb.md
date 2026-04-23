# Strategy A — Opening Range Breakout (ORB)

**FROZEN spec** — Sonnet-3A implements to this contract verbatim.

## Thesis

"대부분의 추세일(trend day)은 세션 오픈 후 최초 15분 안에 결정된다." Binance
USDⓈ-M 영구선물은 24h 시장이므로 "session open"을 **UTC 00:00**으로 정의한다.
(암호자산 관점에서 아시아·유럽·미국 세션 전환이 모두 00:00 UTC 부근에서 발생하며
CME 선물 수요·공급 흐름이 가장 크게 바뀌는 타이밍.)

## Signal Logic (deterministic, no look-ahead)

입력: `BarsData.ohlcv` (UTC DatetimeIndex, 1-분 또는 15-분 봉).

1. 각 UTC 일자 00:00~00:14 (첫 15분)을 그날의 **Opening Range (OR)**로 정의.
   - OR_high = 첫 15분 봉들의 `max(high)`
   - OR_low  = 첫 15분 봉들의 `min(low)`
   - OR_range = OR_high − OR_low
2. **볼륨 필터**: 그날 첫 15분 누적 `volume` 이 직전 20개 거래일 평균 첫-15분
   볼륨의 `volume_multiple` 배 미만이면 **하루 전체 signal=0** (잡음일 제외).
3. **ATR 필터**: 그날 시작 시점의 `ATR14`(일봉 기준)이 `atr_min_pct` × 그날 00:00
   종가 미만이면 **하루 전체 signal=0** (변동성 부족).
4. 00:15 이후:
   - 최초로 `high > OR_high + tick_buffer` 인 봉의 **close_time**에 `side=+1`
     (롱 브레이크아웃). `stop = OR_low`, `target = entry + orr_target_mult * OR_range`.
   - 또는 최초로 `low < OR_low − tick_buffer` 인 봉의 `close_time`에 `side=-1`.
     `stop = OR_high`, `target = entry − orr_target_mult * OR_range`.
   - **하루 한 번만** 발동. 두 번째 브레이크아웃은 무시.
5. 포지션 유지 기간: 당일 종료 시각 `session_end_utc`(기본 23:55) 또는 stop/target
   도달 시까지. `SignalFrame.side`는 포지션 보유 중 내내 ±1 유지.
6. `size_hint`는 항상 `1.0` (Kelly는 상위 RiskSimulator가 담당).

## 파라미터

| 이름 | 기본값 | 허용 범위 | 설명 |
|---|---|---|---|
| `or_minutes` | 15 | 5..60 | Opening range 길이 (분) |
| `session_start_utc` | `"00:00"` | 고정 | UTC 기준 세션 시작 |
| `session_end_utc` | `"23:55"` | 고정 | EOD flatten과 정렬 |
| `volume_multiple` | 1.5 | 1.0..3.0 | OR 볼륨 필터 배수 |
| `volume_lookback_days` | 20 | 5..60 | 평균 볼륨 계산 기간 |
| `atr_min_pct` | 0.01 | 0.005..0.03 | 최소 일봉 ATR/price |
| `tick_buffer` | 0.0 | ≥ 0 | OR 돌파 판정 버퍼 (가격 단위) |
| `orr_target_mult` | 2.0 | 1.0..4.0 | 목표가 배수 (× OR_range) |

Phase 3 튜닝 카운터는 기본값 사용 후에만 증가 (STATUS.md).

## Golden cases (Sonnet-3A가 pytest 변환)

모두 합성 1-분 바, UTC DatetimeIndex, `symbol="TESTUSDT"`, `interval="1m"`.

### GC-A1: OR 형성 → 롱 브레이크아웃
- Bars: 2024-01-01 00:00 ~ 00:30 (31분)
- 가격: 00:00~00:14 동안 close=100±0.5 왕복 (OR_high=100.5, OR_low=99.5, range=1.0).
- 00:15 close=100.6 (돌파), 00:16 close=101.0.
- 볼륨은 충분, ATR 충분이라고 가정 (필터 모두 통과).
- **기대**: `side[0..14]=0`, `side[15]=+1` (00:15의 close_time이 돌파 신호),
  `side[16..30]=+1` (포지션 유지), `stop=99.5`, `target=100.5+2.0*1.0=102.5`.

### GC-A2: 볼륨 필터 불통과 → 종일 flat
- 동일 OHLCV, 단 첫 15분 볼륨이 20일 평균의 1.0배 (< 1.5).
- **기대**: 그 날 모든 바에서 `side=0`.

### GC-A3: 숏 브레이크다운
- 00:00~00:14 OR_high=100.5 OR_low=99.5.
- 00:15 close=99.3 (하단 돌파).
- **기대**: `side[15]=-1`, `stop=100.5`, `target=99.5-2.0*1.0=97.5`.

### GC-A4: stop 히트 후 당일 재진입 없음
- 롱 브레이크아웃 진입 후 00:20 low=99.4 (stop=99.5 관통) → 당일 남은 시간 `side=0` 유지.
- 두 번째 돌파 신호 (예: 00:25 close=100.6) 무시.
- **기대**: `side[15..19]=+1`, `side[20..]=0`, 재진입 없음.

### GC-A5: session_end flatten
- 23:50 롱 포지션 보유 중, 23:55에 `side=0`으로 강제 전환.
- **기대**: `side[...23:54]=+1`, `side[23:55..]=0`. (RiskSimulator의 EOD flatten과
  별개로 Strategy 본체도 session_end에서 포지션 flatten.)

## 구현 제약

- `kairos.backtest.protocols.Strategy` 프로토콜 엄격 준수.
- 리스크/포지션 사이징 금지 — `size_hint=1.0` 고정.
- 볼륨 룩백은 `BarsData.ohlcv.volume`만 사용. 외부 파일 조회 금지.
- ATR14 계산: `kairos.backtest.costs.daily_atr_per_close` 재사용.
- 구현 파일: `kairos/strategies/orb.py`, 테스트: `tests/unit/test_strategy_orb.py`.
- Golden case 5건 + 멱등성(동일 입력→동일 출력) 테스트 1건.
