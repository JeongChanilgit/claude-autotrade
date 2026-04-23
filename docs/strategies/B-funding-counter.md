# Strategy B — Funding Counter

**FROZEN spec** — Sonnet-3B implements to this contract verbatim.

## Thesis

Binance 영구선물 funding rate는 8시간 주기로 롱/숏 중 한쪽이 다른 쪽에 지불.
Funding이 지속적으로 **양수 + 큼** → 롱이 과도하게 붐비고 있음 → 평균회귀 숏 기회.
반대로 지속적으로 **음수 + 큼** → 숏이 과도 → 평균회귀 롱 기회.

단발성 funding 스파이크는 무시하고 **연속 3회 threshold 상회/하회**를 트리거로 사용.
OI 변화율로 추가 확증.

## Signal Logic (deterministic, no look-ahead)

입력: `BarsData.ohlcv` + `BarsData.funding` + `BarsData.metrics` (모두 필수).

1. `funding.funding_rate`를 ohlcv index에 reindex + forward-fill.
   (funding은 8시간 간격, ohlcv는 1분/15분 간격.)
2. **펀딩 임계 트리거**:
   - "최근 3회 funding" 모두 `> +funding_threshold` → **숏 편향**.
   - "최근 3회 funding" 모두 `< −funding_threshold` → **롱 편향**.
   - 그 외 → 편향 없음 (신호 0).
3. **OI 확증**: metrics.sum_open_interest의 24시간 변화율이
   `oi_change_min` 이상일 때만 신호 유효. 그렇지 않으면 편향만 있어도 `side=0`.
4. **진입 타이밍**: 편향 발생 후, 가격이 해당 방향의 역추세 움직임(예: 숏 편향인데 가격
   상승 중)일 때만 진입. 구체적으로:
   - 숏 편향 + `close > SMA(price, entry_sma_window)` → `side=-1`
   - 롱 편향 + `close < SMA(price, entry_sma_window)` → `side=+1`
5. **청산**: `side`의 반대 편향이 발생하거나, `holding_bars` 경과 시 자동 flatten
   (편향 유지 중이어도).
6. `size_hint=1.0`, `stop=NaN`, `target=NaN` (평균회귀 특성상 stop 미사용.
   리스크는 상위 RiskSimulator의 daily/MDD halt로 관리).

## 파라미터

| 이름 | 기본값 | 허용 범위 | 설명 |
|---|---|---|---|
| `funding_threshold` | 0.0005 | 0.0001..0.003 | 0.05%/8h 트리거 레벨 |
| `consecutive_count` | 3 | 2..5 | threshold 연속 상회/하회 횟수 |
| `oi_change_min` | 0.02 | 0.005..0.10 | 24h OI 변화율 최소치 |
| `entry_sma_window` | 60 | 30..240 | 진입 확정용 SMA 창 (분/봉) |
| `holding_bars` | 480 | 60..1440 | 최대 보유 봉 수 (1m 기준 8시간 = 480) |

## Golden cases

모두 합성 bars + 합성 funding + 합성 metrics, 1-분 봉.

### GC-B1: 펀딩 3회 연속 양수 + OI 증가 + 가격 SMA 위 → 숏 진입
- 최근 funding (t-24h, t-16h, t-8h) 각각 +0.10%, +0.08%, +0.07% (모두 > 0.05%).
- 직전 24h OI +5% (> 2% 필터 통과).
- 현재 close > SMA60 (롱 과포지션 + 가격도 과열 상태).
- **기대**: 해당 타임스탬프부터 `side=-1`.

### GC-B2: 펀딩 3회 양수지만 OI 변화 미미 → 신호 없음
- funding 조건 동일.
- OI 변화율 +0.5% (< 2%).
- **기대**: `side=0` 전 구간.

### GC-B3: 펀딩 3회 음수 + 가격 SMA 아래 → 롱 진입
- funding −0.08%, −0.07%, −0.06%.
- OI +3%.
- close < SMA60.
- **기대**: `side=+1`.

### GC-B4: 보유 홀딩바 도달 → 자동 flatten
- GC-B1 셋업으로 숏 진입 후 `holding_bars=480` 경과.
- **기대**: 481번째 봉부터 `side=0`.

### GC-B5: 반대 편향 발생 → flatten
- GC-B1 셋업으로 숏 진입 후 funding이 −0.08% × 3회 연속으로 뒤집힘.
- **기대**: 반대 편향 확인 시점부터 `side=0` (그 시점에 롱 진입 조건 미충족이라면
  flat 유지).

## 구현 제약

- `kairos.backtest.protocols.Strategy` 엄격 준수.
- `data.funding is None` 또는 `data.metrics is None`이면 `ValueError` raise.
- Funding은 `data.funding.reindex(ohlcv.index).ffill()` 공식 패턴 사용.
- 구현: `kairos/strategies/funding.py`, 테스트: `tests/unit/test_strategy_funding.py`.
- Golden case 5건 + 멱등성 테스트 1건.
