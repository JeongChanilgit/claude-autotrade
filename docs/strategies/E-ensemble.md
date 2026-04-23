# Ensemble — Weighted Combination of A/B/C

**FROZEN spec** — Opus-authored after Strategy D drop (ADR-001).

## Thesis

3개 독립 전략의 signal을 weight-blend 하여 단일 `SignalFrame` 생산.
각 전략의 `side * size_hint` 를 가중합한 뒤 threshold 로 이산화.
서브 전략들이 같은 방향으로 과도하게 쏠릴 때 (rolling correlation > `corr_threshold`)
자동으로 weight 를 축소하여 과집중 리스크를 완화.

## Weights (post-ADR-001)

| 전략 | weight |
|---|---|
| A — ORB | 0.40 |
| B — Funding Counter | 0.30 |
| C — Vol Compression | 0.30 |

합 = 1.0. Phase 3 튜닝 카운터는 여기서도 기본값 고정.

## Signal Logic (deterministic)

입력: `BarsData` — ohlcv + funding + metrics **전부 필수** (B/C 가 요구).

1. 각 sub-strategy 를 해당 전략의 기본 params 로 호출 → `SignalFrame` 3개.
2. `signed_size_i = side_i * size_hint_i` (각 sub 의 normalized directional signal).
3. **Rolling pairwise 상관 축소** (look-back = `corr_window` 봉):
   - 각 pair (i, j) 의 rolling Pearson corr `ρ_ij(t)` 계산
   - pair 평균 `ρ̄(t) = mean over pairs`
   - shrink factor `s(t) = clip(1 - max(0, ρ̄(t) - corr_threshold) / (1 - corr_threshold),
     corr_shrink_min, 1.0)`
   - `ρ̄(t)` 가 NaN 이면 (워밍업) `s(t) = 1.0`
4. `weighted(t) = s(t) * Σ w_i * signed_size_i(t)`
5. **이산화**:
   - `weighted(t) > +signal_threshold` → `side=+1`
   - `weighted(t) < -signal_threshold` → `side=-1`
   - 그 외 → `side=0`
   - `size_hint = min(abs(weighted(t)), 1.0)` (side=0 이면 0)
6. `stop=NaN`, `target=NaN` — 개별 전략의 stop/target 은 ensemble 에 전달되지 않음.
   리스크는 RiskSimulator 와 engine 이 담당.

## 파라미터

| 이름 | 기본값 | 허용 범위 | 설명 |
|---|---|---|---|
| `weights` | `{A:0.4, B:0.3, C:0.3}` | 합=1.0 | 전략별 가중치 |
| `corr_window` | 1440 | 240..2880 | rolling corr lookback (1m 기준 1일) |
| `corr_threshold` | 0.6 | 0.3..0.9 | shrink 발동 상관 |
| `corr_shrink_min` | 0.5 | 0.2..1.0 | 최대 축소 한계 |
| `signal_threshold` | 0.15 | 0.05..0.5 | side=±1 판정 컷오프 |

## Golden cases (Opus 손계산)

### GC-E1: 단일 전략만 활성 → 그 전략의 weight 통과
- A 만 side=+1, size=1.0, B/C 는 0.
- weighted = 1.0 * 0.4 = 0.4 > 0.15.
- **기대**: side=+1, size_hint=0.4.

### GC-E2: 두 전략 동방향 → 합산 강화
- A=+1, B=+1, size 각 1.0, C=0.
- weighted = 0.4 + 0.3 = 0.7.
- **기대**: side=+1, size_hint=0.7.

### GC-E3: 두 전략 반대 방향 → 순합 판정
- A=+1, B=-1, size 각 1.0, C=0.
- weighted = 0.4 - 0.3 = 0.1. |0.1| < 0.15.
- **기대**: side=0, size_hint=0.

### GC-E4: 모든 전략 flat → flat
- A=B=C=0.
- **기대**: side=0.

### GC-E5: 상관 축소 발동
- 전 구간 A=B=+1, C=0 (완벽 상관 pair ρ=1.0).
- corr_window 충분히 짧게. 정상 weighted = 0.7.
- shrink: ρ̄ = (1 + 0 + 0)/3 = 0.333 (A-B=1, A-C=0 undefined→NaN→0, B-C=0 undefined→NaN→0);
  **보정**: NaN pair 는 평균 계산에서 제외. 유효 pair 가 하나(A-B)만 있으면 ρ̄=1.0.
  s = clip(1 - (1.0 - 0.6)/(1 - 0.6), 0.5, 1.0) = clip(0, 0.5, 1.0) = 0.5.
- **기대**: weighted = 0.5 * 0.7 = 0.35, side=+1, size_hint=0.35.

## 구현 제약

- `Strategy` 프로토콜 준수. `data.funding` 또는 `data.metrics` 가 None 이면 `ValueError`.
- `weights` 합이 1.0 ± 1e-9 아니면 `ValueError`.
- look-ahead 없음: rolling corr 는 `.rolling().corr()` (과거만 포함).
- sub-strategy 는 constructor 에서 주입 (테스트 용이성). 기본 팩토리는
  `build_default_ensemble()` 가 제공.
- 구현: `kairos/strategies/ensemble.py`, 테스트: `tests/unit/test_strategy_ensemble.py`.
