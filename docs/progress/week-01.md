# 1주차 (2026-11-16 ~ 2026-11-22, 착수 2026-11-12)

## 목표
툴 서버 최소 구성: 거래 이력 조회 툴 하나를 명세하고 구현해, 직접 만든 MCP 클라이언트와 Claude Code 양쪽에서 시점에 맞는 이력을 받는 것까지 검증합니다. (마일스톤 W1, 이슈 #9~#13)

## 완료 항목
| 항목 | 이슈 | 작업자 | 상태 | 검증 방법 |
|---|---|---|---|---|
| `docs/03_mcp_tool_spec.md`: `get_transaction_history` 명세 | #9 | Claude | 문서 | Codex 교차 리뷰 대기 |
| `contracts/mcp/` 입출력 JSON Schema | #9 | Claude | 검증 | `tests/test_contracts.py` 17개 통과 (명세 예시 2개, 유효 4개, 거부 7개, 입력 6개) |
| investigator-ci가 명세 문서 변경에도 실행되도록 경로 추가 | #9 | Claude | 구현 | 이 PR의 CI |

## 결정 변경
- 기준 시점(`as_of`)은 입력으로 받지 않고 대상 거래에서 서버가 도출합니다.
- 조사 범위 바인딩(`FDS_ALLOWED_TARGETS`)으로 허용되지 않은 거래 조회를 서버에서 차단합니다.
- 대상 거래와 같은 초의 다른 거래는 비교 집계에서 제외합니다(보수적 처리).
- 결과 상태를 `ok` / `insufficient` / `empty` / `error`로 나누고, 툴 실행 오류는 `isError: true`로 모델에 전달합니다.
- 금액은 문자열(`"123.45"`), 비율은 숫자로 표현합니다.
- 개발 의존성에 `jsonschema` 추가.

## 기타
- Dependabot #5(Spring Boot 4.1.2), #7(Gradle wrapper 9.9.0)은 사용자가 병합함. backend 빌드 검증은 W4 `backend-ci` 추가 시 확인.

## 다음 작업
1. #10 합성 데이터 (Codex): 명세 4절 시나리오 T1~T12를 재현
2. #11 툴 구현 (Claude)
3. #12 통합 테스트 (Codex), #13 Claude Code 연결 (사람)
