# 01. 데이터셋 명세 (Dataset Spec)

## 1. 데이터 출처
- Kaggle: `kartik2112/fraud-detection`
- 생성 방식: Sparkov Data Generation 도구로 생성한 **시뮬레이션** 신용카드 거래 데이터
- 파일 구성: `fraudTrain.csv`, `fraudTest.csv`
- 기간: 약 2019-01-01 ~ 2020-12-31
- 규모: 학습 약 130만 건, 테스트 약 55만 건, 사기 비율 약 0.5% 내외 (적재 후 실측값으로 갱신 필요)

### 선택 근거
PCA로 익명화된 `V1~V28` 형태의 데이터셋(예: 유럽 카드사 데이터)은 모델 학습에는 적합하지만, 에이전트가 "왜 의심스러운지"를 사람의 언어로 설명할 수 없습니다. 본 데이터는 가맹점명, 카테고리, 좌표, 고객 속성이 원형으로 존재하므로 **근거 기반 보고서**라는 프로젝트 핵심 요구와 일치합니다.

## 2. 필드 명세

| 필드 | 타입(원본) | 의미 | 본 프로젝트에서의 용도 |
|---|---|---|---|
| (index) | int | CSV 행 번호 | 적재 시 제거 |
| `trans_date_trans_time` | datetime 문자열 | 거래 일시 | 시간대 룰, 이력 윈도우 조회 |
| `cc_num` | long | 카드번호 | 카드 식별자 (마스킹/토큰화 후 사용) |
| `merchant` | string | 가맹점명 (`fraud_` 접두사 포함) | 가맹점 리스크 조회 키 |
| `category` | string | 가맹점 업종 (예: `shopping_net`, `grocery_pos`) | 업종별 리스크, 온라인/오프라인 구분 |
| `amt` | decimal | 거래 금액(USD) | 고액 룰, 평소 대비 편차 계산 |
| `first`, `last` | string | 고객 이름 | 표시용 외 사용 안 함 (개인정보 최소화) |
| `gender` | char | 성별 | 사용 안 함 (편향 방지) |
| `street`, `city`, `state`, `zip` | string | 고객 주소 | 표시용 |
| `lat`, `long` | double | 고객 거주지 좌표 | 지리적 이상치 계산 |
| `city_pop` | int | 거주 도시 인구 | 보조 특성 |
| `job` | string | 직업 | 사용 안 함 |
| `dob` | date | 생년월일 | 연령대 파생(선택) |
| `trans_num` | string | 거래 고유번호 | 거래 PK (자연키) |
| `unix_time` | long | 유닉스 타임스탬프 | 거래 간 시간 간격 계산 |
| `merch_lat`, `merch_long` | double | 가맹점 좌표 | 지리적 이상치 계산 |
| `is_fraud` | 0/1 | 정답 라벨 | **런타임 비노출**, 평가용으로만 사용 |

### 라벨 격리 원칙
`is_fraud`는 룰 엔진, MCP 툴, 에이전트 어느 쪽에도 노출하지 않습니다. 노출될 경우 에이전트가 정답을 참조하는 **데이터 누수(leakage)**가 발생하여 조사 능력을 증명할 수 없기 때문입니다. 라벨은 별도 평가 테이블에 두고 룰 정밀도/재현율, 에이전트 판단 정확도 측정에만 사용합니다.

## 3. 전처리 계획

| 단계 | 작업 | 근거 |
|---|---|---|
| 1 | 인덱스 컬럼 제거, 컬럼명 snake_case 유지 | JPA 엔티티 매핑 단순화 |
| 2 | `trans_date_trans_time` → `DATETIME`, `dob` → `DATE` | 기간 조회 인덱스 활용 |
| 3 | `amt`는 `DECIMAL(12,2)`로 적재 | 금액을 부동소수점으로 다루면 반올림 오차 발생 (금융 기본 원칙) |
| 4 | `cc_num`을 해시 기반 `card_token`으로 치환, 원본은 적재하지 않음 | 시뮬레이션이라도 PAN 비저장 원칙을 설계에 반영 (PCI-DSS 관점 어필) |
| 5 | `merchant`의 `fraud_` 접두사 제거 | 모든 가맹점명에 붙어 있어 정보가 없고 표시 품질만 저하 |
| 6 | 고객/가맹점 테이블 분리 (정규화) | 가맹점 리스크 집계, 고객 기준 이력 조회 성능 확보 |
| 7 | `is_fraud`를 `evaluation_label` 테이블로 분리 | 라벨 격리 |
| 8 | 시연용 서브셋 추출 (예: 카드 수백 개 단위) | 로컬 MySQL 적재 시간 및 데모 응답 속도 확보 |
| 9 | 파생 지표 사전 계산(선택): 카드별 평균 금액, 주 이용 지역 | MCP 툴 응답 지연 최소화 |

### 적재 방식
- 대량 적재는 JPA `saveAll`이 아닌 `LOAD DATA INFILE` 또는 JDBC batch insert를 사용합니다.
- 근거: JPA는 엔티티마다 영속성 컨텍스트에 관리 객체를 쌓고, `IDENTITY` 전략에서는 insert 즉시 실행이 강제되어 batch insert가 비활성화됩니다. 백만 건 단위 적재에는 부적합합니다.

## 4. 예상 테이블 구조 (초안)
- `customer` (고객 속성, 거주지 좌표)
- `card` (card_token, customer_id)
- `merchant` (이름, 업종, 좌표)
- `transaction` (trans_num PK, card_token, merchant_id, amt, trans_at, unix_time)
- `evaluation_label` (trans_num, is_fraud)
- `fraud_case`, `investigation_log`, `investigation_report` → 02_architecture.md 참조

## 5. 한계
- 시뮬레이션 데이터이므로 실제 카드사 사기 유형(예: 카드 도용 후 소액 테스트 결제 등)의 분포와 다를 수 있습니다.
- 미국 기반 데이터이므로 국내 가맹점 업종 코드, 원화 금액 체계와는 다릅니다. 필요 시 README에서 국내 체계로의 매핑 방안을 설명합니다.
