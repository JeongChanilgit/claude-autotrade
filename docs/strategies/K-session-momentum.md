# Strategy K — Session Momentum

**FROZEN spec.**

## Thesis

15분봉 기준 UTC 일자의 **첫 봉(들)** 은 해당 세션 전체의 모멘텀 방향을 결정짓는
경향이 강하다. 첫 봉 볼륨이 직전 N일의 평균을 유의미하게 상회할 때 해당 방향을
추종한다.

## Signal Logic (deterministic, no look-ahead)

입력: `BarsData.ohlcv` (UTC DatetimeIndex).

1. 각 UTC 일자의 시작 봉은 `idx.normalize()`가 바뀌는 첫 봉으로 정의.
2. 해당 일자의 첫 `first_bars` 봉 합산 볼륨 `first_vol_d`.
3. 방향(`direction_d`): 첫 `first_bars` 봉 중 **마지막 봉**의 `close`-`open` 부호.
   같으면 0 → 해당 세션 진입 없음.
4. 볼륨 필터: `first_vol_d >= volume_multiple × mean(first_vol over prior
   volume_lookback_days days)`. 최소 1개 이전 일자가 있어야 평균이 정의됨.
5. 진입 봉: 해당 일자 첫 봉 index + `first_bars` (즉 opening window 다음 봉).
6. 보유: 진입 봉부터 해당 UTC 일자의 마지막 봉까지, 또는 `holding_bars` 상한.
   마지막 유지 봉은 `min(enter_at + holding_bars − 1, next_day_start − 1)`.
7. 하루 한 번만 진입, 재진입 없음.
8. `size_hint = 1.0`, `stop = target = NaN`.

## 파라미터

| 이름 | 기본값 | 허용 범위 | 설명 |
|---|---|---|---|
| `first_bars` | 1 | 1..20 | 세션 첫 기준 봉 수 |
| `volume_multiple` | 1.2 | 0.1..10.0 | 첫 봉 볼륨 / 이전 평균 배수 |
| `volume_lookback_days` | 20 | 1..200 | 볼륨 평균 계산 일수 |
| `holding_bars` | 96 | 1..1000 | 최대 보유 봉 수 (세션 내 cap) |

## Golden cases

모두 합성 15-분 봉, UTC DatetimeIndex, `symbol="TESTUSDT"`, `interval="15m"`.

### GC-K1: 롱 진입 (첫 봉 상승 + 볼륨 급증)
3일치 96봉/일 시리즈. 3일차 첫 봉이 close>open이고 볼륨 >= 1.2 × 이전 2일 평균.
**기대**: 3일차 두 번째 봉 (index = 2·96 + 1)에서 `side=+1`.

### GC-K2: 세션 종료 flatten
GC-K1과 동일 구조, `holding_bars` 충분히 크게 설정. 해당 일자 마지막 봉까지 보유.

### GC-K3: 멱등성
같은 입력 두 번 호출 시 SignalFrame 동일.

## 구현 제약

- `kairos.backtest.protocols.Strategy` 프로토콜 엄격 준수.
- 일자 경계는 `idx.normalize()` 기준, 누적/평균은 `shift(1)` 으로 당일 제외.
- 첫 진입 계산은 per-day O(D) 루프, 전체 일자에 대해 O(N) 로 설계한다.
- 구현 파일: `kairos/strategies/session_momentum.py`.
- 테스트 파일: `tests/unit/test_strategy_session_momentum.py`.
