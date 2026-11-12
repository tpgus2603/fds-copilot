# FDS 조사 코파일럿 (FDS Investigation Copilot)

카드 이상거래탐지(FDS) 업무에서 **규칙 기반 1차 탐지 이후의 조사 과정**을 AI 에이전트가 보조하는 시스템입니다. 에이전트는 MCP 툴로 거래 이력, 가맹점 지표, 지리적 이상치를 조회하고, 모든 주장에 근거 ID가 연결된 조사 보고서 초안을 작성합니다. **최종 판단은 사람(조사역)이 내립니다.**

> 현재 상태: 1주차(툴 서버 최소 구성). 주차별 진척은 [docs/progress](docs/progress/README.md)에서 확인할 수 있습니다.

## 업무 흐름

```
규칙 탐지 → 조사 케이스 생성 → 에이전트의 증거 조회(MCP) → 근거가 연결된 보고서 → 조사역 승인·반려
```

## 구성

| 디렉터리 | 역할 | 기술 |
|---|---|---|
| `backend/` | 도메인 백엔드: 룰 엔진, 케이스 상태 관리, 승인·반려 | Spring Boot, JPA, MySQL |
| `investigator/` | AI 조사 서비스와 MCP 툴 서버 (읽기 전용 증거 조회) | Python 3.12, MCP, Claude API, (5주차부터 FastAPI) |
| `frontend/` | 조사역 대시보드: 케이스, 툴 호출 타임라인, 보고서 검토 | React (7주차) |
| `contracts/` | 계층 간 계약: OpenAPI, MCP 툴 입출력 스키마 | |
| `infra/` | 로컬 실행 환경 | Docker Compose |
| `docs/` | 설계 문서와 주차별 진척 기록 | |

## 설계 원칙

- **사람의 최종 결정**: 에이전트는 판정하지 않고 근거와 권고만 제시합니다.
- **최소 권한**: MCP 툴 서버는 읽기 전용 DB 계정만 사용하고, 상태 변경은 도메인 백엔드만 수행합니다.
- **추적 가능성**: 모든 툴 호출과 관찰 결과를 기록하고, 보고서의 주장을 근거 ID에 연결합니다.
- **라벨 격리**: 데이터셋의 정답 라벨(`is_fraud`)은 조사 경로에 노출하지 않고 평가에만 사용합니다.

## 데이터

Kaggle [`kartik2112/fraud-detection`](https://www.kaggle.com/datasets/kartik2112/fraud-detection) (Sparkov로 생성한 시뮬레이션 카드 거래 데이터). 원본은 저장소에 포함하지 않습니다. [data/README.md](data/README.md)를 참고하세요.

## 문서

| 문서 | 내용 |
|---|---|
| [00 프로젝트 헌장](docs/00_project_charter.md) | 목적, 범위, 성공 기준 |
| [01 데이터셋 명세](docs/01_dataset_spec.md) | 필드, 전처리, 라벨 격리 |
| [02 아키텍처](docs/02_architecture.md) | 계층 구성과 데이터 흐름 |
| [03 MCP 툴 명세](docs/03_mcp_tool_spec.md) | 툴 입출력 계약, 결과 상태, 검증 시나리오 |
| [05 로드맵](docs/05_roadmap.md) | 8주 계획 |
| [07 인수인계](docs/07_project_handoff.md) | 최신 설계 기준과 다음 작업 |
| [08 기술 스택 검토](docs/08_tech_stack_review.md) | 기술 선택 근거와 트레이드오프 |

## 개발 방식

이 프로젝트는 Claude와 Codex를 개발 워크플로에 통합해 진행합니다. 작업 규칙은 `AGENTS.md`에 있으며, 모든 변경은 PR과 CI 검증, 사람의 최종 병합을 거칩니다.
