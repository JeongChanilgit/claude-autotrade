# Strategy P — VWAP Target Reversion (R:R)

**FROZEN spec.**

## Thesis

세션 VWAP 대비 **N-σ 이탈**에서 평균회귀를 가정하고 역진입한다. 익절은
VWAP 복귀(**고정 target**), 손절은 target 거리를 **손익비(risk:reward)**로
나눠 자동 산출한다. 전략 내부에서 stop/target 터치를 기준으로 직접 청산한다.

## Signal Logic (deterministic, no look-ahead)

입력: `BarsData.ohlcv` (UTC DatetimeIndex).

1. **세션 VWAP/σ (UTC-day 단위, 누적)**:
   - `typical = (high + low + close) / 3`
   - `vwap = cumsum(typical*volume) / cumsum(volume)` per UTC day
   - `σ² = cumsum((typical - vwap)² * volume) / cumsum(volume)` per UTC day
2. **σ 안정화 게이트**: `session_bar[T] >= min_session_bars`.
3. **롱 진입 (플랫 시)**: `close[T] < vwap[T] - sigma_mult*σ[T]`.
   - `target = vwap[T]` (entry 시점 고정)
   - `D = |target - close[T]|`
   - `stop = close[T] - D / risk_reward`
4. **숏 진입 (플랫 시)**: `close[T] > vwap[T] + sigma_mult*σ[T]` (대칭).
5. **청산 (T+1 이후 봉마다 스캔)**:
   - 롱: `low[t] ≤ stop` → stop 청산. 아니면 `high[t] ≥ target` → target 청산.
   - 숏: 대칭.
   - 한 봉에서 stop/target 동시 터치 시 **stop 우선**.
   - UTC day 경계 교차 시 강제 청산.
   - `holding_bars` 도달 시 강제 청산.
6. 청산 봉부터 `side=0`, `stop=target=NaN`. 보유 중 봉은 진입 시 고정한
   stop/target 값을 그대로 유지한다.
7. `size_hint = 1.0`.

## 파라미터

| 이름 | 기본값 | 허용 범위 | 설명 |
|---|---|---|---|
| `sigma_mult` | 2.0 | 0.5..5.0 | VWAP 편차 σ 배수 임계 |
| `risk_reward` | 1.5 | 0.5..10.0 | 고정 익절 대비 손절 배수(손익비) |
| `holding_bars` | 24 | 1..200 | 최대 보유 봉 수 |
| `min_session_bars` | 4 | 1..50 | σ 안정화 최소 누적 봉 수 |

## Golden cases

모두 합성 15-분 봉, UTC DatetimeIndex, `symbol="TESTUSDT"`, `interval="15m"`.

### GC-P1: 롱 이탈 → target 청산
VWAP 근방 진동 후 깊은 하락으로 `close < vwap - 2σ` → `side[T]=+1`.
다음 봉 high가 VWAP(=target)을 돌파 → 해당 봉에서 `side=0`.

### GC-P2: 롱 이탈 → stop 청산
동일 진입. 다음 봉 low가 stop(= close − D/1.5)을 깨면 해당 봉에서 `side=0`.

### GC-P3: 멱등성
같은 입력 두 번 호출 시 SignalFrame 동일.

## 구현 제약

- `kairos.backtest.protocols.Strategy` 프로토콜 엄격 준수.
- 구현 파일: `kairos/strategies/vwap_target_rr.py`.
- 테스트 파일: `tests/unit/test_strategy_vwap_target_rr.py`.
