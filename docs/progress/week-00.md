# 0주차 (착수 전, 2026-11-11)

## 목표
1주차(MCP 스파이크) 시작 전에 현재 상태를 파악하고 기술 스택의 근거를 정리합니다.

## 완료 항목
| 항목 | 작업자 | 상태 | 검증 방법 |
|---|---|---|---|
| 설계 문서 00, 01, 02, 05, 07 작성 | 사람 + AI | 문서 | - |
| `investigator/` Python 환경(uv, 3.12, mcp, anthropic, pydantic, pytest) | 사람 + AI | 검증 | 임시 MCP 서버로 stdio 초기화, 툴 목록, `ping → pong` 확인 (임시 코드는 저장 안 함) |
| `fdscopliot/` Spring Boot 기본 프로젝트 생성 | 사람 | 구현(기본) | 도메인 코드 없음 |
| 프로젝트 현황 파악 | Claude | 완료 | 폴더와 문서 직접 확인 |
| `08_tech_stack_review.md` 작성 | Claude | 문서 | - |
| 주차별 진척 기록 구조(`docs/progress/`) 도입 | Claude | 문서 | - |
| `08_tech_stack_review.xlsx` 작성 (판정 요약, 대안 비교, 착수 전 조치) | Claude | 문서 | 수식 재계산 오류 0건 |
| Claude Doc "백엔드 역할 분리" 작성 | Claude | 문서 | - |
| `fdscopliot` → `backend` 이름 변경, 루트 `.gitignore`·`README.md` 작성 | Claude | 구현 | - |
| GitHub 저장소 `tpgus2603/fds-copilot` 생성 및 첫 커밋 푸시 | 사람 + Claude | 검증 | `main`에 `eea183a` 푸시 확인 |
| `AGENTS.md`, `CLAUDE.md`, pre-commit 훅, CI(`investigator-ci`, `security`), Dependabot, PR 템플릿 | Claude | 구현 | pre-commit 훅이 `.env` 커밋을 차단하는 것 확인. CI는 이 PR에서 첫 실행 |

## 결정 변경
- 주차별 진척 기록을 `docs/progress/week-NN.md`로 남기기로 함.
- Spring AI 스타터 제거 권장 (모델 호출은 Python 단독 담당). 실제 제거는 Spring 작업 시작 시 수행.
- 서비스 명칭 구분: Spring = 도메인 백엔드, Python+FastAPI = AI 조사 서비스, MCP = 툴 서버. 08 문서 3절 반영.
- 결과 반영 방식 A(Python → Spring API → 상태 전이) 잠정 채택. 조사 로그 경로는 확인 중.
- 형상관리: 모노레포 `fds-copilot`, GitHub Flow, PR 필수, Squash merge, 브랜치 `<작업자>/w<주차>-<주제>`, Conventional Commits. 자동 커밋은 "작업 단위 종료 시 에이전트가 커밋·PR" 규칙(AGENTS.md)으로 운영.
- MCP는 "조건부 유지": 1주차에 같은 서버를 Claude Code와 직접 만든 클라이언트 양쪽에서 호출하는 것을 확인해야 함.

## 확인된 문제
- `03_mcp_tool_spec.md`, `04_agent_workflow.md`는 다른 문서에서 참조하지만 파일이 없음.
- `02_architecture.md`, `05_roadmap.md`에 `07`의 최신 결정(FastAPI 도입 시점, 합성 데이터 우선, 내부 추론 비표시, 시점 통제 등)이 반영되지 않음.
- ~~Git 미초기화.~~ 해결: GitHub 저장소 생성.

## 다음 작업 (1주차)
1. Mac 로컬 폴더를 GitHub 저장소와 연결, `git config core.hooksPath .githooks`
2. `docs/03_mcp_tool_spec.md`에 `get_transaction_history` 명세 작성
3. 합성 데이터 작성 및 MCP 툴 구현
4. 직접 만든 MCP 클라이언트와 Claude Code 양쪽에서 툴 호출 검증
