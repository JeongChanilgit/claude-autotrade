# ARCHITECTURE

인터페이스 계약 누적 문서. **구현 전에 여기 먼저 박아넣는다**. Sonnet 위임 프롬프트에 관련 섹션을 그대로 첨부.

---

## 1. 전체 구조

```
Browser (5173 dev / 8000 prod)
  │
  ▼
FastAPI (kairos.dashboard.api)
  │   ├── REST /api/*
  │   └── WebSocket /ws/backtest/{id}
  ▼
Backtest Core (kairos.backtest)
  │   Engine-A (vectorbt)  ──┐
  │   Engine-B (backtrader) ─┴── Strategy + Costs + Risk + Metrics
  ▼
Storage
  ├── Parquet (원자료, Binance Vision 다운로드분)
  ├── DuckDB  (분석 쿼리 뷰)
  └── SQLite  (백테스트 실행 이력 메타)
```

---

## 2. 데이터 레이어 — FROZEN (2026-04-23 실재 검증 완료)

**Source of truth**: `kairos/data/protocols.py` (Python 측), `kairos/data/paths.py` (경로), `kairos/data/http.py` (HTTP/체크섬/ZIP 공통).
Sonnet 위임 프롬프트에는 반드시 `protocols.py` 전체를 첨부할 것.

### 2.1 확보된 Binance Vision URL 패턴 (2026-04-23 curl HEAD 검증)

| 데이터셋 | 빈도 | URL 템플릿 |
|---|---|---|
| klines (1m/15m) | daily | `data/futures/um/daily/klines/{SYMBOL}/{INTERVAL}/{SYMBOL}-{INTERVAL}-{YYYY-MM-DD}.zip` |
| klines (1m/15m) | monthly | `data/futures/um/monthly/klines/{SYMBOL}/{INTERVAL}/{SYMBOL}-{INTERVAL}-{YYYY-MM}.zip` |
| fundingRate | **monthly only** | `data/futures/um/monthly/fundingRate/{SYMBOL}/{SYMBOL}-fundingRate-{YYYY-MM}.zip` |
| metrics (OI + 롱숏비) | daily | `data/futures/um/daily/metrics/{SYMBOL}/{SYMBOL}-metrics-{YYYY-MM-DD}.zip` |

Base: `https://data.binance.vision/`. 각 ZIP에는 `<url>.CHECKSUM` sidecar (형식: `<sha256>  <filename>` 공백2).

### 2.2 ⚠️ liquidationSnapshot 제거됨 — Phase 3 재결정 필요

`data/futures/um/{daily,monthly}/liquidationSnapshot/` 프리픽스가 **S3 listing에서 완전히 빔**. 2026-04-23 확인. 원 Strategy D 설계가 직접 의존하던 데이터가 사라졌음.

대안 (Phase 3 시작 시 Opus가 사용자 확인 후 결정):
- **(a) aggTrades 기반 합성**: 대형 단방향 시장가 체결 버스트를 "청산 시그니처"로 검출. 풀 히스토리 보장(2026-03까지), 단 BTC 1개월 월별 ZIP ≈ 850MB → 3심볼 5년 ≈ 150GB 디스크·수십억 행 처리.
- **(b) Binance `/fapi/v1/allForceOrders`**: 공식 API, 최근 7일 윈도우 제한 → 백필 불가. 자동 수집 루프 구축 필요(현 프로젝트 범위 밖).
- **(c) 3rd-party 유료 소스**: $0 원칙 위배.
- **(d) Strategy D 폐기, A/B/C 앙상블로 재조정**.

**Phase 1 스코프 결정**: 위 선택은 Phase 3 전에 미룸. Phase 1은 klines / funding / metrics 3종만 다운로드. Strategy D를 (a)로 가는 경우 aggTrades 다운로더를 Phase 3 시작에 추가 개발.

### 2.3 Parquet 스키마 (FROZEN)

자세한 pyarrow 스키마는 `protocols.py`의 `SCHEMA_KLINES / SCHEMA_FUNDING / SCHEMA_METRICS`. 요약:

**klines** (source CSV 12 cols → parquet 13 cols, `ignore` 드롭 + `symbol`·`interval` 추가)
```
open_time(int64 UTC ms) close_time(int64) open/high/low/close(float64) volume(float64)
quote_volume(float64) trade_count(int64) taker_buy_volume(float64)
taker_buy_quote_volume(float64) symbol(string) interval(string)
```

**funding** (source 3 cols → parquet 4 cols, `symbol` 추가)
```
funding_time(int64 UTC ms) funding_interval_hours(int32) funding_rate(float64) symbol(string)
```

**metrics** (source 8 cols → parquet 8 cols; source `create_time`은 "YYYY-MM-DD HH:MM:SS" 문자열이므로 **파싱 필수**)
```
create_time(int64 UTC ms) symbol(string) sum_open_interest(float64) sum_open_interest_value(float64)
count_toptrader_long_short_ratio(float64) sum_toptrader_long_short_ratio(float64)
count_long_short_ratio(float64) sum_taker_long_short_vol_ratio(float64)
```
샘플링 5분 간격.

### 2.4 파티셔닝 규칙 (FROZEN)

Hive-style 디렉토리, `DATA_ROOT = C:\claude-autotrading\data\` (gitignored):

```
data/klines/symbol=BTCUSDT/interval=1m/year=2024/month=03/BTCUSDT-1m-2024-03-15.parquet
data/klines/symbol=BTCUSDT/interval=15m/year=2024/month=03/BTCUSDT-15m-2024-03-15.parquet
data/funding/symbol=BTCUSDT/year=2024/month=03/BTCUSDT-funding-2024-03.parquet
data/metrics/symbol=BTCUSDT/year=2024/month=03/BTCUSDT-metrics-2024-03-15.parquet
```

경로 생성은 **반드시** `kairos.data.paths.parquet_path()` 사용. 직접 `f-string`으로 경로 조립 금지.

### 2.5 DuckDB 뷰 DDL (Sonnet-1D가 구현)

```sql
CREATE OR REPLACE VIEW v_klines_1m AS
SELECT * FROM read_parquet('data/klines/**/*.parquet', hive_partitioning=1)
WHERE interval = '1m';

CREATE OR REPLACE VIEW v_klines_15m AS
SELECT * FROM read_parquet('data/klines/**/*.parquet', hive_partitioning=1)
WHERE interval = '15m';

CREATE OR REPLACE VIEW v_funding AS
SELECT * FROM read_parquet('data/funding/**/*.parquet', hive_partitioning=1);

CREATE OR REPLACE VIEW v_metrics AS
SELECT * FROM read_parquet('data/metrics/**/*.parquet', hive_partitioning=1);
```

### 2.6 다운로더 공통 규약

모든 구현은 `kairos.data.protocols.Downloader` 프로토콜 준수 + 아래 7가지:

1. **멱등성**: 이미 존재하고 비어있지 않은 parquet은 건너뛰기 (`skipped=True`, 네트워크 호출 없음).
2. **체크섬 검증**: `kairos.data.http.verify_sha256()` 사용. 불일치 시 `ChecksumError` + 부분 파일 삭제.
3. **재시도**: `http_get_bytes`가 5xx/timeout만 3회 지수백오프 재시도. 404는 재시도 금지, `skipped=True, rows=0, checksum_ok=False` 반환(갭 로깅용).
4. **포맷**: Snappy 압축 parquet, 문자열 컬럼은 dictionary 인코딩.
5. **파티션**: `paths.parquet_path()` 반환 경로 그대로 기록.
6. **로깅**: `logging.getLogger("kairos.data.download_<dataset>")` 사용.
7. **테스트**: 각 구현은 (a) 짧은 실데이터 1건을 실제로 다운로드하는 `@pytest.mark.integration`, (b) CSV 파싱 단위 테스트, (c) 멱등성 테스트를 포함해야 함.

---

## 3. 백테스트 프로토콜 (Phase 2 선행 작업에서 확정)

> 🟡 **TODO (Phase 2 선행 작업)**: `kairos/backtest/protocols.py`에 아래 프로토콜을 구현한 뒤 Sonnet 위임.

드래프트:

```python
class Strategy(Protocol):
    name: str
    def generate_signals(self, data: BarsData, params: dict) -> SignalFrame: ...

class Engine(Protocol):
    def run(self, strategy, data, params, costs, risk) -> BacktestResult: ...

@dataclass
class BacktestResult:
    equity_curve: pd.Series
    trades: pd.DataFrame
    metrics: dict
    engine_name: str
```

**규칙**:
- 리스크 시뮬레이션(일/주/월 한도, EOD flatten, Kelly 1/4)은 **엔진 위 래퍼**에서만. 각 엔진이 자체 구현하면 교차검증 의미 없음.
- 수량/가격 양자화는 `kairos.backtest.quantize` 한 곳에서만 (Decimal 기반).
- OOS 6개월은 `kairos.data.oos_gate`에서 물리적으로 차단.

---

## 4. API 스펙 (Phase 4 선행 작업에서 확정)

> 🟡 **TODO (Phase 4 선행 작업)**: OpenAPI 스펙 파일로 분리 작성.

드래프트:
- `GET  /api/health` — 생존 확인 (Phase 0 완료)
- `GET  /api/runs` — 실행 이력 리스트
- `POST /api/runs` — 새 백테스트 시작
- `GET  /api/runs/{id}` — 실행 상세
- `GET  /api/runs/{id}/trades` — 거래 리스트 (페이지네이션)
- `POST /api/compare` — 다중 실행 비교
- `WS   /ws/backtest/{id}` — 진행률 스트림 `{progress, stage, log}`

---

## 5. 프론트 라우팅 (Phase 4)

- `/` Home — 실행 목록
- `/run` Run — 백테스트 시작 폼
- `/runs/:id` Results — 상세 결과
- `/compare` Compare — 최대 6개 오버레이
- `/trades` Trades — 전역 거래 검색

---

## 6. 레짐 정의

Phase 3에서 전략을 평가할 4분할 레짐:

| 레짐 | 시기 예시 | 정의 |
|---|---|---|
| 상승추세 | 2020-10 ~ 2021-04 / 2023-10 ~ 2024-03 | 일봉 종가가 SMA200 위 + 30일 수익률 > +20% |
| 박스 | 2023-04 ~ 2023-09 | 30일 수익률 ∣·∣ < 10% + ATR/가격 < 중앙값 |
| 급락 | LUNA 2022-05 / FTX 2022-11 / 2024-08 엔캐리 | 주간 수익률 < -15% |
| 저변동 | 2023-06 ~ 2023-08 | 실현변동성 20일 <연율 40% |

정확한 구간·임계값은 Phase 3에서 Opus가 고정.

---

## 7. 세션 운영

- Sonnet 위임 전: 이 문서의 관련 섹션 + `protocols.py` + 골든 케이스를 프롬프트에 첨부
- Sonnet 산출물은 worktree 격리 후 Opus가 직접 리뷰 → 머지
- 리뷰 체크리스트: lookahead bias / 라이브러리 환각 / Decimal 누락 / 테스트 존재 / 인터페이스 준수
