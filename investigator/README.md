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
| `fds_investigator/agent/openai_client.py` | OpenAI 호환 서버(llama.cpp, Ollama, vLLM) 어댑터 |
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

## 로컬 모델로 실행 (llama.cpp, #28)

API 비용 없이 Mac에서 오픈소스 모델로 조사 루프를 실행합니다. 권장 순서는 9B로 연결을 확인한 뒤 27B로 품질을 비교하는 것입니다.

**1. 설치와 모델 다운로드** (텍스트만 쓰므로 이미지용 `mmproj` 파일은 받지 않습니다. 저장소 전체가 아니라 `.gguf` 파일 하나만 받습니다)

```sh
brew install llama.cpp
mkdir -p ~/models
# 파일 이름은 각 저장소의 Files 목록에서 확인하십시오
uvx --from huggingface_hub hf download unsloth/Qwen3.5-9B-GGUF  Qwen3.5-9B-Q4_K_M.gguf     --local-dir ~/models
uvx --from huggingface_hub hf download unsloth/Qwen3.8-27B-GGUF Qwen3.8-27B-UD-Q4_K_M.gguf --local-dir ~/models   # 2단계
```

**2. 로컬 서버 실행** (별도 터미널, 켜 둔 채로)

```sh
llama-server -m ~/models/Qwen3.5-9B-Q4_K_M.gguf --jinja -c 16384 --port 8080
```

- `--jinja`: 모델의 채팅 템플릿으로 툴 호출을 처리합니다. 없으면 툴 호출이 동작하지 않습니다.
- `-c 16384`: 컨텍스트 창. `local` 프로필의 `context_window`와 같게 둡니다. 8K는 보고서 생성 단계에서 부족합니다.
- 생각 모드는 조사 서비스가 요청마다 끕니다(`--thinking`으로 켤 수 있음). 서버 쪽에서도 끄려면 llama-server의 reasoning 관련 옵션을 사용하십시오(버전마다 이름이 다를 수 있음).

**3. 조사 실행**

```sh
uv run --locked fds-investigate high --provider openai --model qwen3.5-9b --db ./local.db --out ./trace-local-high.json
```

- `--provider openai`면 `local` 프로필(300초, 컨텍스트 16,384)이 적용됩니다.
- Ollama를 쓴다면 `--backend ollama --base-url http://localhost:11434/v1`을 붙입니다. 생각 끄기 필드가 `reasoning_effort: "none"`으로 바뀌고, 토큰화 API가 없어 문자 수로 추정합니다(최선 노력).
- 샘플링 기본값은 백엔드마다 다릅니다(#32, PR #33 리뷰). `llama_cpp`는 검증한 `qwen-instruct`(temperature 0.7, top_p 0.8, top_k 20, min_p 0, presence_penalty 1.5)와 `parallel_tool_calls: false`를 보내고, `ollama`·`generic`은 확장 필드를 지원 목록에 명시하지 않으므로 아무것도 보내지 않습니다(`server`). 필요하면 `--sampling qwen-instruct`로 명시 선택합니다. 모델을 비교할 때는 같은 프리셋으로 실행합니다. 실행 기록의 `sampling`에 적용 값이 남습니다.
- 서버의 컨텍스트 길이를 16,384가 아닌 값으로 띄웠다면 `--context-window <같은 값>`으로 맞춥니다. `max_tokens`(2,000)보다 커야 합니다.
- 결과 출력의 `elapsed_seconds_approx`로 시간을, `outcome.reason`으로 미완료 사유를 확인합니다. 처음에는 `high`, `usual`, `new` 사례를 권합니다.

## 평가 세트 실행 (#26)

`fds-investigate`와 같은 모델 인자를 받습니다. 사례별로 툴 서버를 새로 띄우고 허용 목록에는 그 사례의 대상 거래만 넣습니다.

```sh
uv run --locked fds-eval --provider openai --model qwen3.8-27b --db ./local.db --label 27b-v0 --out ./eval-27b-v0.json
uv run --locked fds-eval ... --repeat 3 --only high,inject_scope --out ./eval-27b-rep.json   # 일부 사례 반복
```

- 진행 상황은 사례마다 한 줄씩(표준 오류) 나오고, 끝나면 요약 지표를 출력합니다.
- 결과 파일에는 지표 행, 요약, 실행 기록 전체가 들어갑니다. `eval-*.json`은 Git에 올리지 않습니다(`.gitignore`). 기록할 값은 `docs/progress/`에 옮겨 적습니다.
- 지표: 최종 보고율, 기대 보고율, 첫 제출 통과율(분모: 제출이 있던 실행), 권고 일치율, 기대 충족률(그룹별), 미완료 사유 분포, 필수 근거 누락, **필수 툴 미호출·사례별 툴 사용**, 범위 밖 조회 시도, 인젝션 표식 기재, 수정 요청, 툴 호출, 평균 토큰, 지연.

## 문제 해결

### macOS: `ModuleNotFoundError: No module named 'fds_investigator'`

**증상**: 설치는 정상인데(`Built… Installed 1`) 실행하면 패키지를 찾지 못합니다.

**원인**: 편집 가능 설치는 `site-packages/_editable_impl_fds_investigator.pth` 한 줄 파일로 `src` 경로를 `sys.path`에 추가합니다. CPython은 보안 수정 이후 숨김 속성(macOS `UF_HIDDEN`)이 붙은 `.pth` 파일을 건너뜁니다. 저장소를 `~/Desktop` 아래에 둔 환경에서 `.venv`와 그 안의 파일에 숨김 속성이 반복해서 다시 붙는 현상이 관찰되었습니다 (2026-11-14).

**확인**:
```sh
ls -lO .venv/lib/python3.12/site-packages/*.pth    # hidden이 보이면 이 문제
```

**해결**: 가상환경을 숨김 속성이 붙지 않는 위치로 옮깁니다. 이 변수는 현재 터미널 세션에만 적용되므로 새 터미널마다 다시 실행합니다. `~/.zshrc`에 넣으면 모든 uv 프로젝트가 같은 가상환경을 공유해 충돌하므로 넣지 않습니다.
```sh
export UV_PROJECT_ENVIRONMENT="$HOME/.venvs/fds-investigator"
uv sync --locked
uv run --locked python -c "import fds_investigator, sys; print(sys.prefix)"   # ~/.venvs/fds-investigator 이면 정상
```
`chflags nohidden <파일>`로 속성을 지워도 일시적으로는 해결되지만, 속성이 다시 붙어 재발했습니다.
