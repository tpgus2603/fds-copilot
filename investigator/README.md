# investigator

AI 조사 서비스와 증거 조회용 MCP 툴 서버(Python 3.12, `uv`)입니다.

## 구조

| 모듈 | 역할 |
|---|---|
| `fds_investigator/server.py` | MCP 툴 서버 (stdio). 계약 스키마 노출, 핸들러 측 입력 검증, `CallToolResult` 직접 반환 |
| `fds_investigator/service.py` | 요청 처리: 입력 검증 → 허용 목록 → 존재 확인 → 이력 조회, 오류 변환 |
| `fds_investigator/history.py` | 집계 규칙 (순수 함수, `docs/03` 3.4·3.6절) |
| `fds_investigator/repository.py` | 거래 저장소 인터페이스와 SQLite 구현 (읽기 전용 모드) |
| `fds_investigator/config.py` | 설정과 허용 목록 (fail closed) |
| `fds_investigator/contracts.py` | `contracts/mcp/` 스키마 로드 |
| `fds_investigator/load_csv.py` | Kaggle 형식 CSV → SQLite 적재 (라벨·개인정보 제거, 카드 토큰화) |
| `fds_investigator/report_rules.py` | 보고서 검증 V1(숫자 문자)·V4(값 비교) 기준 구현 |
| `fds_investigator/agent/loop.py` | 조사 루프: 기본 증거, 상한, 마감 시각, 제출 규칙, 미완료 사유 (`docs/04`) |
| `fds_investigator/agent/model_client.py` | 모델 호출 인터페이스와 Anthropic 구현 (재시도·시도 횟수 직접 관리) |
| `fds_investigator/agent/tool_session.py` | 조사 1건 전용 툴 서버 세션 (대상 거래 하나로 범위 고정) |
| `fds_investigator/agent/render.py`, `verify.py` | 렌더링 카탈로그, 보고서 검증 V1~V7 |
| `fds_investigator/agent/cli.py` | `fds-investigate` 명령 |

## 실행

```sh
uv sync --locked
uv run --locked pytest -q                      # 단위 + 실제 stdio MCP 호출 테스트

# 데이터 적재 (Kaggle Sparkov 형식 CSV)
uv run --locked python -m fds_investigator.load_csv ../data/fixtures/transactions.csv ./local.db

# 툴 서버 실행 (stdio)
FDS_ALLOWED_TARGETS=<대상 거래 ID,...> FDS_DB_PATH=./local.db uv run --locked fds-evidence-tools
```

### 환경 변수

| 변수 | 필수 | 설명 |
|---|---|---|
| `FDS_ALLOWED_TARGETS` | 예 | 이번 조사에서 허용된 대상 거래 ID(쉼표 구분). 없거나 비었거나 형식이 틀리면 **시작 거부**. `*`는 `FDS_ENV=dev`/`test`에서만 허용 |
| `FDS_DB_PATH` | 예 | SQLite 파일 경로. 읽기 전용으로 엽니다 |
| `FDS_ENV` | 아니오 | `dev`, `test` |
| `FDS_QUERY_TIMEOUT_SECONDS` | 아니오 | 조회 시간 제한, 기본 5초 |
| `FDS_CARD_TOKEN_SALT` | 적재 시 | 카드 토큰 솔트. 미지정 시 개발용 고정값 |
| `FDS_CONTRACTS_DIR` | 아니오 | 계약 스키마 경로 재지정 |

### Claude Code에 연결 (#13)

저장소 루트에서 다음처럼 등록하면 Claude Code가 같은 툴 서버를 호출할 수 있습니다.

```sh
claude mcp add fds-evidence \
  -e FDS_ALLOWED_TARGETS='*' -e FDS_ENV=dev -e FDS_DB_PATH="$PWD/investigator/local.db" \
  -- uv --directory "$PWD/investigator" run --locked fds-evidence-tools
```

## 조사 루프 실행 (Claude API)

합성 데이터를 적재한 뒤, 거래 한 건을 조사합니다. 실제 Claude API를 호출하므로 비용이 듭니다.

```sh
uv run --locked python -m fds_investigator.load_csv ../data/fixtures/transactions.csv ./local.db

export ANTHROPIC_API_KEY=...        # 저장소·.env 파일에 쓰지 말고 셸 환경 변수로만
export CLAUDE_MODEL=<모델명>
uv run --locked fds-investigate high --db ./local.db --out ./trace-high.json
```

- 표준 출력: 실행 ID, 결과(`reported`/`incomplete` + 사유), 사용량, 렌더링된 보고서
- `--out`: 전체 실행 기록(단계별 툴 호출·결과 스냅샷·토큰). `*.json`은 커밋하지 마십시오(로컬 실험 기록)
- 종료 코드: `reported`면 0, 미완료면 1, 설정 누락이면 2
- 조사 1건당 상한: 모델 호출 6회, 조회 8회, 120초, 입력 토큰 60,000 (`docs/04` 2.4)

키는 툴 서버 프로세스로 전달되지 않고 실행 기록에도 남지 않습니다. `.env.example`은 자동으로 로드되지 않습니다.
