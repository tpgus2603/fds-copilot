# FDS 조사 코파일럿 Python 환경

Python 3.12와 `uv`로 MCP 서버와 Claude API 클라이언트의 실행 환경을 관리합니다.

```sh
uv sync --locked
uv run --locked python --version
uv run --locked python -c 'from mcp.server.fastmcp import FastMCP; from anthropic import AsyncAnthropic; from pydantic import BaseModel'
```

로컬 MCP 테스트에는 API 키가 필요하지 않습니다. 실제 Claude 호출을 추가할 때는
`.env.example`을 참고해 `ANTHROPIC_API_KEY`와 `CLAUDE_MODEL`을
에이전트 프로세스의 환경 변수로 전달합니다. `.env.example`은 설정 예시이며 자동으로 로드되지 않습니다.
