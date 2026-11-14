# 09. 테스트 전략 (Test Strategy)

기준일: 2026-11-14. 현재 구현된 Python 계층의 테스트 구조와, 앞으로 추가할 Spring Boot·FastAPI·React 계층의 테스트 방식을 정의합니다.

## 1. 공통 원칙

| 원칙 | 내용 | 이유 |
|---|---|---|
| 계약이 기준 | 계층 간 형식은 `contracts/`의 스키마로 검증합니다. 문서에 실린 예시도 테스트가 직접 읽어 스키마와 대조합니다. | 문서, 계약, 구현이 서로 어긋나면 CI가 실패해야 합니다. |
| 기대값은 독립적으로 | 합성 데이터의 기대값은 구현과 다른 작업자가 명세 규칙으로 계산합니다(#10). | 구현 코드로 기대값을 만들면 같은 버그가 양쪽에 들어가 테스트가 통과합니다. |
| 양성과 음성을 함께 | 거부해야 할 입력과 **통과해야 할 경계 입력**을 함께 둡니다. | 음성 테스트만 늘리면 정상 입력까지 거부하는 과잉 제약을 놓칩니다(#20 재리뷰). |
| 테스트도 검증 | 수정을 되돌렸을 때 새 테스트가 실패하는지 확인합니다(변이 확인). | 실패하지 않는 테스트는 결함을 잡는다고 말할 수 없습니다. |
| 외부 의존은 경계에서 대역으로 | 저장소, LLM, 다른 서비스는 인터페이스 경계에서 대역(fake/mock)으로 바꿉니다. 장애 주입도 같은 경계에서 합니다. | 결정적(deterministic)이고 빠른 테스트를 위해서입니다. |
| CI는 LLM을 호출하지 않음 | 실제 모델 호출은 수동 평가로만 합니다(`AGENTS.md`). | LLM 응답은 매번 달라 통과·실패 판정에 쓸 수 없고, 비용이 듭니다. |
| 실제 경로는 최소한 한 번 | 대역으로만 검증하지 않고, 실제 프로세스·프로토콜을 통과하는 테스트를 계층마다 몇 개 둡니다. | SDK·프레임워크의 실제 동작이 가정과 다를 수 있습니다(MCP SDK 입력 검증 사례). |

## 2. 현재: Python 계층 (`investigator/`, 130개)

### 2.1 구성

| 층 | 파일 | 수 | 무엇을 | 대역 |
|---|---|---|---|---|
| 계약 | `test_contracts.py` | 63 | MCP 툴 입출력 스키마. 명세 03의 예시 통과, 상태·표본 모순·라벨 누출·잘못된 재시도 값 거부, 반올림 경계 통과 | 없음 |
| 계약 | `test_report_contract.py` | 16 | 보고서 스키마. 명세 04 예시 통과, 그 예시가 합성 데이터 기대값과 일치, 판정·추론 필드 거부 | 없음 |
| 단위 | `test_history.py` | 15 | 집계 규칙 순수 함수. 반올림, 중앙값, p95, 비율, 정렬, 시점 범위 | 없음 (I/O 없음) |
| 단위 | `test_config.py` | 10 | 허용 목록 fail closed 7가지, `*` 허용 조건 | 없음 |
| 단위 | `test_load_csv.py` | 2 | 적재 시 라벨·개인정보 제거, 카드 토큰화 | 임시 SQLite |
| 서비스 | `test_service.py` | 17 | 검사 순서, 결과 상태, 오류 변환, 장애 주입, 저장소 하한 누락 방어 | `FakeRepo`, `LeakyRepo` |
| 실제 프로토콜 | `test_server_stdio.py` | 7 | 서버를 별도 프로세스로 띄워 MCP 클라이언트로 호출. 계약 노출, 구조화 오류, 미등록 툴 JSON-RPC 오류, 시작 거부 | 임시 SQLite |

```
          ▲ 느림, 실제 경로
          │  실제 프로토콜 (stdio MCP)            7
          │  서비스 (저장소 대역, 장애 주입)       17
          │  단위 (순수 함수, 설정, 적재)          27
          │  계약 (스키마 ↔ 명세 ↔ 합성 데이터)    79
          ▼ 빠름, 결정적
```

### 2.2 설계 포인트

- **집계 로직을 순수 함수로 분리**해 DB 없이 계산 규칙만 검증합니다. 같은 규칙을 저장소가 실수해도 순수 함수가 다시 강제하는지를 `LeakyRepo`로 확인합니다.
- **장애 주입은 저장소 경계에서** 합니다. `FakeRepo`가 연결 예외나 지연을 일으키면 서비스가 `DATA_SOURCE_UNAVAILABLE`·`TIMEOUT`으로 변환하는지 봅니다.
- **실제 MCP 경로 테스트**는 `subprocess`로 서버를 띄우고 SDK 클라이언트로 호출합니다. SDK 기본 설정에서는 오류가 텍스트로만 반환된다는 사실을 이 층에서만 잡을 수 있었습니다.
- 비동기 코드는 `anyio`의 pytest 플러그인(`@pytest.mark.anyio`)으로 실행합니다.

### 2.3 실행

```sh
cd investigator
uv run --locked pytest -q                       # 전체
uv run --locked pytest -q tests/test_history.py # 파일 하나
uv run --locked pytest -q -k lookback           # 이름으로 선택
```

CI(`investigator-ci`)는 `investigator/`, `contracts/`, `docs/03`, `docs/04`, `data/fixtures/`가 바뀐 PR에서 실행됩니다.

### 2.4 예정 (W1~W2)

| 이슈 | 추가 테스트 |
|---|---|
| #12 | 합성 데이터 T1~T15 전체를 실제 MCP로 검증하는 통합 테스트. T11 장애 주입, T15 시작 거부 |
| #15 | 조사 루프: 정해진 응답을 순서대로 돌려주는 가짜 모델 클라이언트 + 실제 툴 서버. 상한 도달, `max_tokens`·`refusal`, 보고서 검증 V1~V6 |

## 3. 예정: Spring Boot 도메인 백엔드 (W4~W5)

도구: JUnit 5, AssertJ, Spring Boot Test, Testcontainers(MySQL), Mockito

| 층 | 대상 | 방법 | 핵심 |
|---|---|---|---|
| 단위 | 케이스 상태 전이 규칙 | 스프링 없이 순수 JUnit. 허용 전이 표를 매개변수 테스트로 전수 확인 | 허용되지 않은 전이(예: `CLOSED_FRAUD → INVESTIGATING`)가 예외인지 |
| 단위 | 규칙 판정 로직 | 순수 JUnit (집계 결과를 입력으로 받는 함수) | 경계값 (금액 임계값과 같은 값, 1 큰 값) |
| 영속성 | 리포지토리, 규칙 SQL | `@DataJpaTest` + **Testcontainers MySQL** | H2는 MySQL 방언·락 동작이 달라 거짓 통과 위험. 합성 데이터로 탐지 건수 기대값 대조 |
| 영속성 | 낙관적 락 | 두 개의 독립 트랜잭션(`TransactionTemplate`)이 같은 케이스를 종결 | 두 번째가 `ObjectOptimisticLockingFailureException` |
| 이벤트 | `AFTER_COMMIT` 조사 요청 | `@SpringBootTest` + 조사 서비스 클라이언트를 `@MockitoBean`으로 대체 | 커밋되면 호출, 롤백되면 호출되지 않음 |
| 웹 | 컨트롤러 | `@WebMvcTest` + `MockMvc` | 요청 검증, 오류 응답 형식, 응답이 `contracts/`의 OpenAPI 스키마를 만족 |
| 외부 호출 | AI 조사 서비스 HTTP 클라이언트 | `MockRestServiceServer` 또는 WireMock | 타임아웃, 5xx, 응답 지연 시 `INCOMPLETE` 전환 |
| 통합 | 탐지 → 케이스 → 조사 요청 → 결과 수신 → `REPORTED` | `@SpringBootTest` + Testcontainers, 조사 서비스는 WireMock | 결과 중복 전달 시 멱등 처리 |

**트랜잭션 경계를 테스트할 때의 주의점**

- `@DataJpaTest`와 `@Transactional`이 붙은 테스트는 테스트 메서드 전체를 하나의 트랜잭션으로 감싸고 **끝나면 롤백**합니다. 이 때문에 두 가지 문제가 생깁니다.
  1. **`AFTER_COMMIT` 리스너가 실행되지 않습니다.** 커밋이 일어나지 않기 때문입니다. 이벤트 테스트는 `@Transactional`을 쓰지 않고 실제로 커밋하거나, `TestTransaction.flagForCommit()`과 `TestTransaction.end()`로 커밋 시점을 직접 만듭니다.
  2. **낙관적 락 충돌이 재현되지 않습니다.** 한 영속성 컨텍스트 안에서는 같은 엔티티가 1차 캐시의 같은 인스턴스이므로 버전 충돌이 나지 않습니다. 충돌은 서로 다른 트랜잭션, 즉 서로 다른 영속성 컨텍스트 두 개로 재현합니다.
- 쓰기 지연 때문에 `flush()` 전에는 SQL이 실행되지 않습니다. 제약 조건 위반처럼 DB가 판단하는 오류는 `flush()` 후에 단언합니다.

**CI**: `backend-ci` 워크플로를 추가합니다(#16). 실행 조건은 `backend/**`, `contracts/**`가 바뀐 PR입니다. 단계는 JDK 17, Gradle 캐시, `./gradlew test`입니다. GitHub의 Ubuntu 러너에는 Docker가 있어 Testcontainers를 그대로 쓸 수 있습니다. Dependabot의 Spring Boot·Gradle PR도 이때부터 빌드로 검증됩니다.

## 4. 예정: FastAPI AI 조사 서비스 (W5)

도구: pytest, `httpx.AsyncClient` + `ASGITransport`, `respx`(HTTP 대역)

| 층 | 대상 | 방법 | 핵심 |
|---|---|---|---|
| 웹 | 조사 요청 API | `ASGITransport`로 앱을 직접 호출 (서버 기동 없음) | 요청 검증, 즉시 `202 Accepted` 반환, 같은 요청 ID 재요청 시 멱등 |
| 의존성 교체 | 모델 클라이언트, 툴 서버 실행기 | FastAPI `dependency_overrides`로 가짜 구현 주입 | 루프 로직은 #15의 가짜 모델 클라이언트를 재사용 |
| 외부 호출 | 도메인 백엔드로 결과 전달(콜백) | `respx`로 Spring API를 흉내 | 콜백 실패 시 재시도, 재시도 후에도 실패하면 기록 보존 |
| 백그라운드 | 조사 실행 작업 | 작업 완료를 기다린 뒤 콜백 내용 단언 | 응답은 빨리, 조사는 비동기로 |
| 계약 | 요청·응답 모델 | Pydantic 모델이 생성한 OpenAPI를 `contracts/openapi.yaml`과 비교 | 양쪽 서비스가 같은 계약을 따르는지 |

## 5. 서비스 간 검증: 계약 테스트 우선

Spring과 FastAPI를 매번 함께 띄우는 E2E 테스트는 느리고 불안정합니다. 그래서 다음 순서로 검증합니다.

1. **계약 테스트(양쪽)**: Spring 응답과 FastAPI 요청이 모두 `contracts/`의 같은 스키마를 만족하는지 각자의 CI에서 확인합니다(소비자·제공자 양쪽 검증).
2. **대역 통합 테스트(각자)**: 상대 서비스는 WireMock(Spring 쪽)과 respx(FastAPI 쪽)로 대체합니다.
3. **E2E 스모크(소수)**: `docker compose`로 MySQL, Spring, FastAPI, 툴 서버를 함께 띄우고, 가짜 모델 클라이언트로 케이스 1~2건이 `REPORTED`까지 가는지 확인합니다. W8에 추가하며, PR마다가 아니라 main 병합 시나 수동으로 실행합니다.

## 6. 예정: React 검토 화면 (W7)

| 층 | 방법 |
|---|---|
| 컴포넌트 | Vitest + React Testing Library. 툴 호출 타임라인, 근거 링크, 종결 폼 |
| API 대역 | MSW(Mock Service Worker)로 백엔드 응답 흉내 |
| 스모크 | Playwright로 케이스 목록 → 보고서 → 종결 흐름 1개 |

## 7. 실제 모델 평가 (W2 일부, W6, W8)

실제 Claude 호출은 테스트가 아니라 **평가**로 다룹니다.

- 실행: 로컬 또는 `workflow_dispatch` 수동 워크플로. 키는 GitHub Secrets나 로컬 환경 변수로만 전달합니다.
- 비용 통제: 케이스 수와 조사 상한(04 명세 2.4)을 고정합니다.
- 결과: 통과·실패가 아니라 다음 지표를 기록합니다. 미완료 사유 분포, 검증 실패 유형(V1~V6), 툴 호출 수, 토큰, 지연, 보고서 품질(사람 검토).
- 8주차 A/B/C 비교 실험도 같은 방식으로 실행합니다.

## 8. 도입 일정 요약

| 주차 | 추가되는 테스트 | CI |
|---|---|---|
| W1 (현재) | 계약, 단위, 서비스, 실제 MCP (130) + #12 통합 | `investigator-ci` |
| W2 | 조사 루프(가짜 모델), 보고서 검증기 | 동일 |
| W4 | Spring 단위, `@DataJpaTest` + Testcontainers | `backend-ci` 추가 |
| W5 | Spring 이벤트·락·통합, FastAPI 웹·의존성 교체, 양쪽 계약 | 두 CI |
| W7 | React 컴포넌트, Playwright 스모크 | `frontend-ci` 추가 |
| W8 | docker compose E2E 스모크, A/B/C 평가 | 수동·main 병합 시 |
