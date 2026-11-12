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
| 조사 범위 바인딩 | 툴 서버는 시작 시 이번 조사에서 허용된 대상 거래 ID 목록(`FDS_ALLOWED_TARGETS`, 쉼표 구분)을 받습니다. 목록에 없는 ID는 `OUT_OF_SCOPE` 오류입니다. 상세는 2.1절. | 모델이 인자를 바꿔 다른 고객의 데이터를 조회하는 것을 서버가 막습니다. 프롬프트로 막는 것은 신뢰할 수 없습니다. |
| 시점 통제 | 기준 시점(`as_of`)은 대상 거래의 거래 시각이며, 서버가 정합니다. 입력으로 받지 않습니다. | 모델이 기준 시점을 바꿔 미래 정보를 조회할 수 없게 합니다. |
| 수치는 코드가 계산 | 평균, 비율, 백분위 등 모든 수치는 서버가 계산해 돌려줍니다. | 모델의 산술 오류를 막고, 보고서 수치를 툴 결과와 대조할 수 있게 합니다. |
| 금액 표현 | 금액은 소수 둘째 자리까지의 **문자열**(`"123.45"`)로 표현합니다. 비율과 점유율은 숫자입니다. | JSON 숫자는 파서에서 부동소수점으로 읽혀 반올림 오차가 생길 수 있습니다. |
| 근거 ID | 결과마다 `evidence_id`를 붙이고, 개별 거래는 거래 ID(`trans_num`)로 참조합니다. | 보고서의 주장을 근거에 연결하기 위해서입니다. |
| 결과 상태 | `ok` / `insufficient` / `empty` / `error` 중 하나 | 빈 결과와 조회 실패를 구분합니다. |
| 비노출 필드 | 카드 원번호, 고객 이름·주소·생년월일, 정답 라벨(`is_fraud`)은 반환하지 않습니다. | 개인정보 최소화, 라벨 격리 |
| 시각 표현 | `YYYY-MM-DDTHH:MM:SS` (시간대 없음, 데이터셋 원본 시각 기준) | 원본 데이터에 시간대 정보가 없습니다. 임의로 붙이지 않습니다. |

### 2.1 조사 범위 바인딩과 검사 순서

**허용 목록은 기본적으로 닫혀 있습니다(fail closed).**

| `FDS_ALLOWED_TARGETS` 상태 | 동작 |
|---|---|
| 설정되지 않음, 빈 문자열, 공백만 있음 | **서버 시작 거부** (종료 코드 1, 표준 오류에 사유 출력) |
| 형식 오류 (ID 패턴에 맞지 않는 항목 포함) | **서버 시작 거부** |
| `*` | 제한 없음. `FDS_ENV=dev` 또는 `FDS_ENV=test`가 함께 설정된 경우에만 허용하고, 그 외에는 시작 거부 |
| `id1,id2,...` | 목록의 ID만 허용 |

설정 누락이 "전체 허용"으로 이어지면, 운영 중 환경 변수 하나가 빠졌을 때 모든 고객 데이터가 조회 가능해집니다. 그래서 잘못 설정된 상태에서는 시작하지 않는 쪽을 택했습니다. 실행 중 요청마다 거부하는 방식보다 문제가 배포 시점에 바로 드러납니다.

**요청 처리 순서**

1. 입력 검증: 계약 스키마(`contracts/mcp/*.input.schema.json`) 위반 → `INVALID_ARGUMENT`
2. 허용 목록 검사: 목록에 없음 → `OUT_OF_SCOPE`
3. 대상 거래 조회: 없음 → `NOT_FOUND`
4. 이력 조회와 집계: 연결 실패 → `DATA_SOURCE_UNAVAILABLE`, 시간 초과 → `TIMEOUT`

허용 목록을 존재 확인보다 **먼저** 검사합니다. 반대 순서이면 허용되지 않은 ID에 대해 `NOT_FOUND`와 `OUT_OF_SCOPE` 중 어느 것이 오는지로 해당 거래가 존재하는지 알아낼 수 있습니다. 순서를 고정하면 허용 범위 밖의 ID는 존재 여부와 관계없이 항상 `OUT_OF_SCOPE`입니다.

### 2.2 입력 검증과 오류 반환 경로 (구현 요구사항)

Python MCP SDK(`mcp` 1.30)의 저수준 서버는 기본적으로 핸들러 호출 전에 `inputSchema`로 입력을 검증합니다. 실패하면 `"Input validation error: ..."` **텍스트만** 담은 오류 결과를 돌려주고 `structuredContent`는 비어 있습니다. 또한 핸들러가 dict를 반환하면 SDK가 `isError: false`로 고정합니다. 이대로는 이 명세의 구조화된 오류 계약을 지킬 수 없으므로 구현(#11)은 다음을 따릅니다.

- `tools/list`에는 계약 입력 스키마를 그대로 노출합니다. 모델이 제약을 알 수 있어야 하기 때문입니다.
- SDK의 입력 검증은 끄고(`@server.call_tool(validate_input=False)`), 핸들러 안에서 같은 계약 스키마로 검증합니다.
- 핸들러는 `CallToolResult`를 직접 만들어 반환합니다. `structuredContent`에 결과를 넣고, `status: "error"`일 때 `isError: true`로 설정합니다.
- 반환 직전에 출력 스키마로 자체 검증합니다. 실패하면 원래 결과 대신 `INTERNAL` 오류를 반환하고 로그를 남깁니다. 계약을 위반한 결과는 모델에 전달하지 않습니다.
- 결과는 원본 레코드를 그대로 직렬화하지 않고, 허용된 필드만 골라 새로 구성합니다. 출력 스키마의 `additionalProperties: false`는 키 누출을 잡는 보조 수단일 뿐, 값에 섞인 정보까지 막지는 못합니다.

## 3. `get_transaction_history`

### 3.1 목적

대상 거래가 이 카드의 평소 이용 패턴과 비교해 어떤지 판단할 근거를 제공합니다. 대상 거래 시점 이전의 일정 기간 거래를 집계해 금액 분포, 업종 분포, 최근 거래 목록을 돌려줍니다.

### 3.2 모델에게 보이는 설명 (`description`)

```
대상 거래의 금액이나 업종이 이 카드의 평소 이용 패턴과 다른지 확인할 때 사용합니다.
대상 거래 시점 이전의 비교 기간(기본 90일, lookback_days로 7~365일 지정) 거래를 집계해
금액 통계(건수, 평균, 중앙값, 최대값, 20건 이상일 때만 95백분위), 대상 금액이 평균의 몇 배인지,
업종 분포, 선택한 비교 기간 안에서 대상 업종을 이용한 적이 있는지, 최근 거래 최대 10건을 돌려줍니다.
대상 거래 자체와 그 이후 거래는 포함되지 않으므로, 비교 기간 밖의 이용 이력은 알 수 없습니다.
status가 insufficient이면 표본이 4건 이하라 통계를 단정적으로 해석하면 안 되고,
empty이면 비교 기간에 거래가 없다는 뜻이며, error이면 근거를 확보하지 못한 것입니다.
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
- 조회 조건은 `trans_at < as_of`입니다. 따라서 대상 거래와 **같은 시각(초 단위)**의 다른 거래는 제외되고, 제외 건수를 `quality.notes`에 기록합니다. 원본 데이터는 초 단위까지만 기록되어 같은 초의 거래 중 무엇이 먼저인지 입증할 수 없습니다. `unix_time`도 초 단위라 순서를 알려주지 못하고, 거래 ID는 무작위 문자열이라 순서 정보가 없습니다. 실제 선후를 확인할 수 있는 원천 필드가 생기면 이 정책을 다시 검토합니다.
- `recent_transactions`의 정렬은 `trans_at` 내림차순, 같으면 `transaction_id` 오름차순입니다. 이 보조 정렬은 결과를 재현 가능하게 만들기 위한 것이며 실제 선후를 뜻하지 않습니다.

### 3.5 출력 (`structuredContent`)

```json
{
  "tool": "get_transaction_history",
  "schema_version": "1.1",
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
| `evidence_id` | `EV-TH-<대상 거래 ID>-<lookback_days>d`. 보고서는 이 ID와 필드 경로로 인용합니다(예: `EV-TH-a1b2c3d4e5-90d#stats.target_to_mean_ratio`). **한 번의 조사 실행 안에서만 유일한 참조**입니다. 데이터를 다시 적재하면 같은 ID의 내용이 달라질 수 있으므로, 조사 서비스는 툴 결과 전체를 조사 기록에 스냅샷으로 저장하고 보고서의 근거는 그 스냅샷 기준으로 검증합니다. |
| `basis` | 이 결과가 어떤 조건으로 계산되었는지 기록합니다. 재현과 감사에 필요합니다. |
| `target` | 비교 대상인 거래의 요약입니다. 결과 하나만 보고도 비교가 가능하도록 포함합니다. |
| `stats.amount_p95` | 금액 95백분위. 표본이 20건 이상일 때만 값이 있고, 미만이면 `null`입니다. 표본이 적으면 백분위가 사실상 최댓값과 같아 의미가 없기 때문입니다. 계산 방식은 3.6절. |
| `stats.target_to_mean_ratio` | 대상 금액 ÷ 평균 금액. 평균이 0이면 `null`. 계산 방식은 3.6절. |
| `stats.target_exceeds_max` | 대상 금액이 비교 기간 최댓값보다 **큰지**(같으면 `false`) |
| `category_distribution` | 건수 내림차순, 같으면 업종명 오름차순, 상위 10개 업종 |
| `target_category` | **선택한 비교 기간 안에서** 대상 거래 업종을 이용했는지. 기간 밖의 이용 여부는 알 수 없습니다. `seen_before`가 `true`이면 `count ≥ 1`이고 `last_seen_at`이 있으며, `false`이면 `0`과 `null`입니다. |
| `recent_transactions` | 기준 시점에 가까운 순으로 최대 10건. 각 `transaction_id`도 근거로 인용할 수 있습니다. |
| `quality.notes` | 해석에 주의할 점(예: 같은 시각 거래 제외 건수) |

### 3.6 집계 규칙

합성 데이터(#10)의 기대값을 구현(#11)과 독립적으로 계산할 수 있도록 계산 방식을 고정합니다. 모든 금액 연산은 10진수(Python `Decimal`)로 수행합니다.

| 항목 | 규칙 |
|---|---|
| 반올림 | **ROUND_HALF_UP**(사사오입). 은행가 반올림(ROUND_HALF_EVEN)은 쓰지 않습니다. |
| `amount_mean` | 금액 합 ÷ 건수, 소수 둘째 자리 반올림 |
| `amount_median` | 오름차순 정렬 후 가운데 값. 짝수 건이면 가운데 두 값의 평균을 소수 둘째 자리 반올림 |
| `amount_p95` | **nearest-rank 방식**: 오름차순 정렬 후 `ceil(0.95 × n)`번째 값(1부터 셈). 보간하지 않으므로 항상 실제 거래 금액 중 하나입니다. n ≥ 20일 때만 계산 |
| `amount_max` | 최댓값 |
| `target_to_mean_ratio` | 대상 금액 ÷ **반올림 전** 평균, 소수 둘째 자리 반올림. 반올림 전 평균이 0이면 `null`이고 `quality.notes`에 사유 기록 |
| `share` | 업종 건수 ÷ 전체 건수, 소수 넷째 자리 반올림. 반올림 때문에 합이 정확히 1이 아닐 수 있습니다. |

p95 계산 방식을 명시한 이유는 방식마다 결과가 다르기 때문입니다. 예를 들어 n = 20이면 nearest-rank는 19번째 값이지만, 선형 보간 방식은 19번째와 20번째 값 사이의 값을 냅니다. nearest-rank를 고른 이유는 결과가 항상 실제 거래 금액 중 하나라서 "이 카드에서 실제로 있었던 금액"으로 설명할 수 있기 때문입니다.

### 3.7 결과 상태

| `status` | 조건 | `stats` | `isError` | 보고서에서의 처리 |
|---|---|---|---|---|
| `ok` | 비교 기간 거래 5건 이상 | 평균·중앙값·최댓값·초과 여부 모두 값 있음. `amount_p95`는 20건 이상일 때만 값 | false | 통계를 근거로 인용 |
| `insufficient` | 1~4건 | `ok`와 같되 `amount_p95`는 항상 `null` | false | 표본이 적다는 사실을 함께 기록 |
| `empty` | 0건 | `txn_count`는 0, 나머지 수치 `null`. 업종 분포와 최근 거래는 빈 배열, `target_category`는 `false / 0 / null` | false | "비교 기간 이력 없음"을 정보 부족으로 기록. 신규 카드일 수 있으며 그 자체가 정보입니다. |
| `error` | 조회 불가 | 근거 필드 없음. `tool`, `schema_version`, `status`, `error`만 허용 | **true** | 근거 미확보. 필수 근거라면 조사 미완료 |

5건은 통계적 신뢰도를 보증하는 기준이 아니라 **1주차 운영 기준**입니다. "평균을 근거로 인용해도 될 최소한의 건수"로 정한 값이며, Kaggle 데이터를 적재한 4주차에 카드별 거래 수 분포를 보고 다시 정합니다.

`empty`와 `error`를 구분하는 것이 핵심입니다. "거래가 없었다"는 사실은 근거가 되지만, "조회하지 못했다"는 근거가 되지 않습니다. 둘을 섞으면 DB 장애가 "이력 없는 카드"로 둔갑해 잘못된 판단으로 이어질 수 있습니다.

### 3.8 오류

`status: "error"`일 때 `error` 객체를 채웁니다.

```json
{
  "tool": "get_transaction_history",
  "schema_version": "1.1",
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

`retryable`은 오류 코드로 결정되며 서버가 임의로 바꾸지 않습니다. 일시적 장애(`DATA_SOURCE_UNAVAILABLE`, `TIMEOUT`)만 `true`이고 나머지는 모두 `false`입니다. 출력 스키마가 이 대응을 강제합니다. AI 조사 서비스는 이 값을 보고 재시도 여부를 정하며, 재시도는 모델이 아니라 조사 서비스가 정한 횟수 안에서 수행합니다. `OUT_OF_SCOPE`처럼 다시 시도해도 결과가 같은 오류를 재시도하면 호출 비용만 늘어납니다.

### 3.9 모델에게 보내는 텍스트 (`content`)

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
| T8 | 허용 목록에 **포함된**, 존재하지 않는 거래 ID | `error`, `NOT_FOUND`, `isError: true` |
| T9 | **존재하는** 거래지만 허용 목록에 없는 ID | `error`, `OUT_OF_SCOPE` (존재 여부와 무관) |
| T10 | 실제 `tools/call`로 `lookback_days: 400` 전송 | `isError: true`, `structuredContent.error.code == "INVALID_ARGUMENT"` (SDK 텍스트 오류가 아님) |
| T11 | DB 연결 실패 | `error`, `DATA_SOURCE_UNAVAILABLE`, `retryable: true` (`empty`가 아님) |
| T12 | 응답에 비노출 필드 없음 | `is_fraud`, `cc_num`, 고객 이름·주소가 `structuredContent`와 `content` 텍스트 어디에도 없음 |
| T13 | 표본 경계: 4/5건, 19/20건 | 4건 `insufficient`, 5건 `ok`·p95 `null`, 19건 p95 `null`, 20건 p95 = 19번째 값 |
| T14 | 비교 기간 평균이 0.00 (금액 0 거래만 존재) | `target_to_mean_ratio: null`, `quality.notes`에 사유 |
| T15 | 허용 목록 설정 누락·빈 값·형식 오류, `FDS_ENV` 없이 `*` | 서버 시작 거부 |

**시나리오별 담당**

| 시나리오 | 구현 방법 | 담당 이슈 |
|---|---|---|
| T1~T7, T12~T14 | 합성 거래 fixture와 기대값 | #10 |
| T8~T10 | 같은 fixture에 허용 목록과 호출 인자를 조합 | #10(데이터), #12(호출) |
| T11 | fixture가 아니라 **장애 주입**: 저장소 연결·읽기 경계에서 드라이버 연결 예외를 던지는 대역을 stdio 테스트 서버에 주입하고, 실제 MCP 응답을 확인 | #12 |
| T15 | 환경 변수 조합으로 서버 프로세스를 띄워 종료 코드 확인 | #12 |

## 5. 결정 사항과 남은 질문

**결정**
- 기준 시점은 입력으로 받지 않고 대상 거래에서 도출합니다.
- 같은 초의 거래는 제외합니다(`trans_at < as_of`).
- 표본 기준(1주차 운영 기준): 5건 미만은 `insufficient`, 20건 미만은 `amount_p95` 생략.
- 금액은 문자열, 비율은 숫자로 표현합니다.
- 허용 목록은 fail closed, 검사 순서는 입력 → 허용 목록 → 존재 → 조회입니다.
- p95는 nearest-rank, 반올림은 ROUND_HALF_UP, 비율 분모는 반올림 전 평균입니다.
- 입력 검증과 오류 변환은 SDK가 아니라 툴 핸들러가 담당합니다(2.2절).
- `evidence_id`는 조사 실행 안에서의 참조이며, 툴 결과 스냅샷을 함께 저장합니다.
- 리뷰 반영 이력: PR #20 Codex 리뷰 (스키마 버전 1.0 → 1.1)

**남은 질문**
- 조사 범위 바인딩을 환경 변수로 할지, 2주차 조사 루프에서 세션 단위로 넘길지는 #15 구현 시 확정합니다. 어느 방식이든 2.1절의 fail closed와 검사 순서는 유지합니다.
- 표본 기준(5건, 20건)은 Kaggle 데이터를 적재한 4주차에 카드별 거래 수 분포를 보고 다시 검토합니다.

## 6. 예정된 툴 (3주차)

| 툴 | 목적 | 주의 |
|---|---|---|
| `get_merchant_profile` | 가맹점·업종의 과거 탐지 비중 등 참고 지표 | "사기율"이 아니라 "과거 1차 탐지 비중"으로 표현. 라벨 기반 지표는 사용하지 않음 |
| `compute_geo_anomaly` | 거주지와 가맹점 간 거리, 직전 거래 대비 이동 속도 | 가맹점 좌표를 고객의 실측 위치로 해석하지 않음 |
