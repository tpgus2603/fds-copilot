# 03. MCP 툴 명세 (MCP Tool Spec)

이 문서는 툴 서버가 제공하는 MCP 툴의 입출력 계약을 정의합니다. 1주차에는 `get_transaction_history` 하나를 정의하고, 3주차에 가맹점·업종 지표와 지리적 이상치 툴을 추가합니다. 기계가 검증하는 스키마는 `contracts/mcp/`에 있으며, 이 문서와 스키마가 다르면 스키마가 기준입니다.

## 1. 기초: MCP 툴 호출이 동작하는 방식

1. **툴 목록 조회 (`tools/list`)**: 클라이언트(AI 조사 서비스 또는 Claude Code)가 서버에 툴 목록을 요청합니다. 서버는 툴마다 `name`, `description`, `inputSchema`(JSON Schema)를 돌려줍니다.
2. **모델에 전달**: 클라이언트는 이 정보를 Claude API 요청의 `tools`로 넘깁니다. 모델은 `description`을 읽고 언제 이 툴을 쓸지 판단하며, `inputSchema`에 맞춰 인자를 만듭니다. 따라서 **`description`은 모델에게 주는 사용 설명서**이고, 품질이 툴 선택의 정확도에 직접 영향을 줍니다.
3. **툴 실행 (`tools/call`)**: 모델이 `tool_use`를 반환하면 클라이언트가 같은 이름과 인자로 서버에 `tools/call`을 보냅니다.
4. **결과 반환**: 서버는 결과를 `content`(모델이 읽을 텍스트)와 `structuredContent`(코드가 검증할 JSON)로 돌려줍니다. 실행 중 문제가 생기면 결과에 `isError: true`를 표시합니다.

**오류의 두 층위**

| 층위 | 예 | 표현 | 누가 처리 |
|---|---|---|---|
| 프로토콜 오류 | 존재하지 않는 툴 이름, JSON-RPC 형식 오류 | JSON-RPC `error` 응답 | 클라이언트 코드 (모델에 전달하지 않음) |
| 툴 실행 오류 | 대상 거래 없음, 조사 범위 밖, DB 연결 실패 | 정상 결과 + `isError: true` + `status: "error"` | 모델이 읽고 다음 행동을 결정할 수 있음 |

툴 실행 오류를 결과로 돌려주는 이유는 모델이 "이 근거는 확보하지 못했다"는 사실을 알고 보고서에 정보 부족으로 기록하거나 다른 조회를 시도할 수 있게 하기 위해서입니다.

## 2. 모든 툴의 공통 규칙

| 규칙 | 내용 | 이유 |
|---|---|---|
| 읽기 전용 | 툴은 데이터를 조회만 합니다. | 최소 권한 원칙 (`02_architecture.md` 2.1) |
| 조사 범위 바인딩 | 툴 서버는 시작 시 이번 조사에서 허용된 대상 거래 ID 목록(`FDS_ALLOWED_TARGETS`)을 받습니다. 목록에 없는 ID는 `OUT_OF_SCOPE` 오류입니다. 값이 `*`이면 제한하지 않습니다(개발·테스트 전용). | 모델이 인자를 바꿔 다른 고객의 데이터를 조회하는 것을 서버가 막습니다. 프롬프트로 막는 것은 신뢰할 수 없습니다. |
| 시점 통제 | 기준 시점(`as_of`)은 대상 거래의 거래 시각이며, 서버가 정합니다. 입력으로 받지 않습니다. | 모델이 기준 시점을 바꿔 미래 정보를 조회할 수 없게 합니다. |
| 수치는 코드가 계산 | 평균, 비율, 백분위 등 모든 수치는 서버가 계산해 돌려줍니다. | 모델의 산술 오류를 막고, 보고서 수치를 툴 결과와 대조할 수 있게 합니다. |
| 금액 표현 | 금액은 소수 둘째 자리까지의 **문자열**(`"123.45"`)로 표현합니다. 비율과 점유율은 숫자입니다. | JSON 숫자는 파서에서 부동소수점으로 읽혀 반올림 오차가 생길 수 있습니다. |
| 근거 ID | 결과마다 `evidence_id`를 붙이고, 개별 거래는 거래 ID(`trans_num`)로 참조합니다. | 보고서의 주장을 근거에 연결하기 위해서입니다. |
| 결과 상태 | `ok` / `insufficient` / `empty` / `error` 중 하나 | 빈 결과와 조회 실패를 구분합니다. |
| 비노출 필드 | 카드 원번호, 고객 이름·주소·생년월일, 정답 라벨(`is_fraud`)은 반환하지 않습니다. | 개인정보 최소화, 라벨 격리 |
| 시각 표현 | `YYYY-MM-DDTHH:MM:SS` (시간대 없음, 데이터셋 원본 시각 기준) | 원본 데이터에 시간대 정보가 없습니다. 임의로 붙이지 않습니다. |

## 3. `get_transaction_history`

### 3.1 목적

대상 거래가 이 카드의 평소 이용 패턴과 비교해 어떤지 판단할 근거를 제공합니다. 대상 거래 시점 이전의 일정 기간 거래를 집계해 금액 분포, 업종 분포, 최근 거래 목록을 돌려줍니다.

### 3.2 모델에게 보이는 설명 (`description`)

```
조사 대상 거래의 카드에 대해, 대상 거래 시점 이전의 거래 이력을 집계합니다.
금액 통계(건수, 평균, 중앙값, 95백분위, 최대), 대상 거래 금액이 평소 대비 몇 배인지,
업종 분포와 대상 거래 업종의 과거 이용 여부, 최근 거래 최대 10건을 돌려줍니다.
대상 거래 자체와 그 이후 거래는 집계에 포함되지 않습니다.
status가 insufficient이면 표본이 적어 통계의 신뢰도가 낮고, empty이면 비교 기간에 거래가 없습니다.
결과의 수치를 인용할 때는 evidence_id를 함께 적으십시오.
```

### 3.3 입력

| 필드 | 타입 | 필수 | 기본값 | 제약 | 설명 |
|---|---|---|---|---|---|
| `target_transaction_id` | string | 예 | - | 1~64자, 영숫자 | 조사 대상 거래 ID (`trans_num`) |
| `lookback_days` | integer | 아니오 | 90 | 7 이상 365 이하 | 기준 시점 이전 비교 기간(일) |

`lookback_days`는 모델이 고를 수 있게 둡니다. 예를 들어 최근 이용 변화를 보려면 짧게, 업종 이용 여부를 보려면 길게 조회할 수 있습니다. 범위를 벗어나면 서버가 값을 고치지 않고 `INVALID_ARGUMENT` 오류를 돌려줍니다. 조용히 고치면 모델은 자신이 요청한 기간으로 조회되었다고 오해합니다.

### 3.4 조회 범위 (서버가 강제)

- 대상 카드: 대상 거래의 `card_token`과 같은 거래만
- 기간: `as_of - lookback_days` 이상, `as_of` **미만**
- `as_of`: 대상 거래의 거래 시각
- 대상 거래 자체는 제외합니다.
- 대상 거래와 **같은 시각(초 단위)**의 다른 거래도 제외합니다. 원본 데이터만으로는 같은 초의 거래 중 어느 것이 먼저인지 알 수 없으므로, 미래 정보가 섞이지 않는 쪽으로 보수적으로 처리합니다.

### 3.5 출력 (`structuredContent`)

```json
{
  "tool": "get_transaction_history",
  "schema_version": "1.0",
  "status": "ok",
  "evidence_id": "EV-TH-a1b2c3d4e5-90d",
  "basis": {
    "target_transaction_id": "a1b2c3d4e5",
    "card_token": "card_7f3e9a",
    "as_of": "2020-06-21T02:13:44",
    "window_start": "2020-03-23T02:13:44",
    "lookback_days": 90
  },
  "target": {
    "trans_at": "2020-06-21T02:13:44",
    "amount": "1240.00",
    "category": "shopping_net",
    "merchant_name": "Kuhn LLC"
  },
  "stats": {
    "txn_count": 42,
    "amount_mean": "61.30",
    "amount_median": "48.75",
    "amount_p95": "180.20",
    "amount_max": "212.00",
    "target_to_mean_ratio": 20.23,
    "target_exceeds_max": true
  },
  "category_distribution": [
    { "category": "grocery_pos", "count": 18, "share": 0.4286 },
    { "category": "gas_transport", "count": 12, "share": 0.2857 }
  ],
  "target_category": {
    "seen_before": false,
    "count": 0,
    "last_seen_at": null
  },
  "recent_transactions": [
    { "transaction_id": "f9e8d7c6b5", "trans_at": "2020-06-20T18:02:11", "amount": "35.10", "category": "grocery_pos", "merchant_name": "Lind-Buckridge" }
  ],
  "quality": {
    "min_sample_required": 5,
    "notes": []
  }
}
```

**필드 설명**

| 필드 | 설명 |
|---|---|
| `evidence_id` | `EV-TH-<대상 거래 ID>-<lookback_days>d`. 같은 입력이면 같은 ID입니다. 보고서는 이 ID와 필드 경로로 인용합니다(예: `EV-TH-a1b2c3d4e5-90d#stats.target_to_mean_ratio`). |
| `basis` | 이 결과가 어떤 조건으로 계산되었는지 기록합니다. 재현과 감사에 필요합니다. |
| `target` | 비교 대상인 거래의 요약입니다. 결과 하나만 보고도 비교가 가능하도록 포함합니다. |
| `stats.amount_p95` | 금액 95백분위. 표본이 20건 미만이면 `null`입니다. 표본이 적으면 백분위가 사실상 최댓값과 같아 의미가 없기 때문입니다. |
| `stats.target_to_mean_ratio` | 대상 금액 ÷ 평균 금액, 소수 둘째 자리 반올림 |
| `stats.target_exceeds_max` | 대상 금액이 비교 기간 최댓값보다 큰지 여부 |
| `category_distribution` | 건수 내림차순, 상위 10개 업종. `share`는 소수 넷째 자리 |
| `target_category` | 대상 거래 업종의 과거 이용 여부. 처음 쓰는 업종인지는 조사에서 자주 확인하는 항목입니다. |
| `recent_transactions` | 기준 시점에 가까운 순으로 최대 10건. 각 `transaction_id`도 근거로 인용할 수 있습니다. |
| `quality.notes` | 해석에 주의할 점(예: 같은 시각 거래 제외 건수) |

### 3.6 결과 상태

| `status` | 조건 | `stats` | `isError` | 보고서에서의 처리 |
|---|---|---|---|---|
| `ok` | 비교 기간 거래 5건 이상 | 모두 채움 | false | 통계를 근거로 인용 |
| `insufficient` | 1~4건 | 채우되 `amount_p95`는 `null` | false | 표본이 적다는 사실을 함께 기록 |
| `empty` | 0건 | 모든 수치 `null`, `txn_count`는 0 | false | "비교 기간 이력 없음"을 정보 부족으로 기록. 신규 카드일 수 있으며 그 자체가 정보입니다. |
| `error` | 조회 불가 | 없음 | **true** | 근거 미확보. 필수 근거라면 조사 미완료 |

`empty`와 `error`를 구분하는 것이 핵심입니다. "거래가 없었다"는 사실은 근거가 되지만, "조회하지 못했다"는 근거가 되지 않습니다. 둘을 섞으면 DB 장애가 "이력 없는 카드"로 둔갑해 잘못된 판단으로 이어질 수 있습니다.

### 3.7 오류

`status: "error"`일 때 `error` 객체를 채웁니다.

```json
{
  "tool": "get_transaction_history",
  "schema_version": "1.0",
  "status": "error",
  "error": {
    "code": "OUT_OF_SCOPE",
    "message": "대상 거래가 이번 조사 범위에 포함되지 않습니다.",
    "retryable": false
  }
}
```

| `code` | 조건 | `retryable` |
|---|---|---|
| `INVALID_ARGUMENT` | 입력이 스키마나 범위를 벗어남 | false |
| `NOT_FOUND` | 대상 거래 ID가 없음 | false |
| `OUT_OF_SCOPE` | 허용된 대상 거래 목록에 없음 | false |
| `DATA_SOURCE_UNAVAILABLE` | DB 연결 실패 | true |
| `TIMEOUT` | 조회 시간 초과 (기본 5초) | true |
| `INTERNAL` | 그 밖의 예기치 않은 오류. 내부 메시지는 노출하지 않고 로그에만 남김 | false |

`retryable`은 AI 조사 서비스가 재시도할지 판단하는 데 씁니다. 재시도는 모델이 아니라 조사 서비스가 정한 횟수 안에서 수행합니다.

### 3.8 모델에게 보내는 텍스트 (`content`)

`structuredContent`와 같은 JSON을 텍스트로 직렬화해 보냅니다. 요약문을 따로 만들지 않습니다. 요약문을 만들면 그 문장 자체가 해석이 되어, 모델이 원래 수치 대신 요약문의 표현을 인용할 수 있기 때문입니다.

## 4. 검증 시나리오

합성 데이터(#10)와 통합 테스트(#12)는 아래 시나리오를 모두 포함합니다.

| # | 시나리오 | 기대 결과 |
|---|---|---|
| T1 | 평소 소액 결제 카드의 고액 거래 | `ok`, `target_to_mean_ratio`가 큼, `target_exceeds_max: true` |
| T2 | 평소 패턴과 유사한 거래 | `ok`, 비율이 1에 가깝고 `target_category.seen_before: true` |
| T3 | 비교 기간 거래 3건 | `insufficient`, `amount_p95: null` |
| T4 | 비교 기간 거래 0건 | `empty`, `isError: false` |
| T5 | 다른 카드 거래가 같은 기간에 존재 | 다른 카드 거래가 집계에 포함되지 않음 |
| T6 | 대상 거래 이후 거래 존재 | 이후 거래가 집계에 포함되지 않음 |
| T7 | 대상 거래와 같은 초의 다른 거래 | 제외되고 `quality.notes`에 기록 |
| T8 | 존재하지 않는 거래 ID | `error`, `NOT_FOUND`, `isError: true` |
| T9 | 허용 목록에 없는 거래 ID | `error`, `OUT_OF_SCOPE` |
| T10 | `lookback_days: 400` | `error`, `INVALID_ARGUMENT` |
| T11 | DB 연결 실패 | `error`, `DATA_SOURCE_UNAVAILABLE`, `retryable: true` (`empty`가 아님) |
| T12 | 응답에 비노출 필드 없음 | `is_fraud`, `cc_num`, 고객 이름·주소가 결과 어디에도 없음 |

## 5. 결정 사항과 남은 질문

**결정**
- 기준 시점은 입력으로 받지 않고 대상 거래에서 도출합니다.
- 같은 초의 거래는 제외합니다(보수적 처리).
- 표본 기준: 5건 미만은 `insufficient`, 20건 미만은 `amount_p95` 생략.
- 금액은 문자열, 비율은 숫자로 표현합니다.

**남은 질문**
- 조사 범위 바인딩을 환경 변수로 할지, 2주차 조사 루프에서 세션 단위로 넘길지는 #15 구현 시 확정합니다.
- 표본 기준(5건, 20건)은 Kaggle 데이터를 적재한 4주차에 카드별 거래 수 분포를 보고 다시 검토합니다.

## 6. 예정된 툴 (3주차)

| 툴 | 목적 | 주의 |
|---|---|---|
| `get_merchant_profile` | 가맹점·업종의 과거 탐지 비중 등 참고 지표 | "사기율"이 아니라 "과거 1차 탐지 비중"으로 표현. 라벨 기반 지표는 사용하지 않음 |
| `compute_geo_anomaly` | 거주지와 가맹점 간 거리, 직전 거래 대비 이동 속도 | 가맹점 좌표를 고객의 실측 위치로 해석하지 않음 |
