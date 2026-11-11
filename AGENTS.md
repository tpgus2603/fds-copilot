# AGENTS.md — AI 에이전트 공통 작업 규칙

이 파일은 이 저장소에서 작업하는 모든 AI 에이전트(Codex, Claude 등)와 사람이 따르는 규칙입니다.

## 1. 작업 전에 읽을 것
1. `docs/progress/` 의 최신 `week-NN.md` (현재 진척과 다음 작업)
2. `docs/07_project_handoff.md` (최신 설계 기준)
3. 작업 영역에 해당하는 설계 문서 (`docs/00`~`09`)

## 2. 프로젝트 원칙 (위반 금지)
- 에이전트는 판정하지 않고 근거와 권고만 제시합니다. 최종 판단은 조사역(사람)입니다.
- MCP 툴 서버는 읽기 전용입니다. 업무 상태 변경은 `backend/`(도메인 백엔드)만 수행합니다.
- `is_fraud` 라벨은 조사 경로(룰, MCP 툴, 에이전트 프롬프트)에 절대 노출하지 않습니다. 평가 코드에서만 사용합니다.
- 조사 대상 거래 시점 이후의 데이터와 대상 거래 자체를 비교 집계에 포함하지 않습니다.
- 카드 원번호(PAN)를 모델에 보내거나 저장하지 않습니다.
- 금액은 부동소수점이 아닌 `DECIMAL`/`BigDecimal`/`Decimal`로 다룹니다.

## 3. 브랜치와 커밋
- `main`에 직접 커밋하지 않습니다. 모든 변경은 PR로 병합합니다.
- 브랜치 이름: `<작업자>/w<주차>-<주제>` (예: `codex/w1-history-tool`, `claude/w1-mcp-spec`, `me/w4-rule-engine`)
- 커밋 메시지: Conventional Commits
  - 형식: `<type>(<scope>): <요약>`
  - type: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `ci`
  - scope: `backend`, `investigator`, `frontend`, `contracts`, `infra`, `docs`
- 커밋은 의미 있는 작업 단위로 나눕니다. 깨진 상태(테스트 실패)로 커밋하지 않습니다.
- AI 에이전트가 작성한 커밋에는 `Co-Authored-By` 트레일러로 에이전트를 명시합니다.

## 4. 작업 종료 절차 (Definition of Done)
1. 해당 영역의 테스트와 린트를 로컬에서 통과시킵니다.
   - investigator: `cd investigator && uv run --locked pytest`
   - backend: `cd backend && ./gradlew test`
2. `docs/progress/week-NN.md`에 완료 항목을 기록합니다. **구현 완료**와 **검증 완료**를 구분하고 검증 방법을 적습니다.
3. 설계 결정이 바뀌었다면 해당 설계 문서를 함께 수정하고 progress의 "결정 변경"에 기록합니다.
4. 커밋 → 푸시 → PR 생성. PR 본문에 무엇을, 왜, 어떻게 검증했는지 적습니다.
5. 병합은 사람이 합니다. 에이전트는 PR을 스스로 병합하지 않습니다.

## 5. 이슈 관리
- 모든 작업은 GitHub Issue에서 시작합니다. 이슈 없이 시작한 작업은 먼저 이슈를 만듭니다.
- 이슈에는 라벨 네 종류를 붙입니다: `type:`(종류), `area:`(영역), `owner:`(담당 하나), `priority:`(P0 필수 / P1 목표 / P2 여유)
- 마일스톤은 주차(`W1`~`W8`)입니다. 이번 주에 하지 않을 작업은 다음 주차로 옮깁니다.
- 선행 작업이 있으면 본문에 `선행: #번호`를 적고 `status: blocked`를 붙입니다. 선행 작업이 병합되면 라벨을 뗍니다.
- 설계 선택이 필요한 논의는 `decision` 라벨 이슈로 남기고, 결정되면 설계 문서에 반영한 뒤 닫습니다.
- PR 본문에 `Closes #번호`를 적어 병합 시 이슈가 자동으로 닫히게 합니다.
- 브랜치 이름에 이슈 번호를 포함합니다: `<작업자>/w<주차>-<번호>-<주제>` (예: `claude/w1-9-tool-spec`)

## 6. 협업 충돌 방지
- 이슈마다 담당자를 하나만 둡니다. 다른 담당자의 이슈 영역은 수정하지 않고 코멘트로 요청합니다.
- 같은 컴퓨터에서 여러 에이전트가 동시에 작업하면 `git worktree`로 작업 폴더를 분리합니다.
- 다른 에이전트의 PR은 교차 리뷰합니다 (Codex PR → Claude 리뷰, Claude PR → Codex 리뷰).

## 7. 보안
- `.env`, API 키, 인증서, Kaggle 원본 CSV를 커밋하지 않습니다. pre-commit 훅이 이를 검사합니다.
- 최초 클론 후 한 번 실행: `git config core.hooksPath .githooks`
- 기본 CI는 Claude API를 호출하지 않습니다. 에이전트 루프는 가짜(mock) 클라이언트로 테스트합니다.

## 8. 응답 언어
- 문서와 PR 설명은 한국어 합니다체로 작성합니다. 코드 식별자와 커밋 type/scope는 영어를 사용합니다.
