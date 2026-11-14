"""조사 1건 전용 툴 서버 세션 (docs/04 2.3).

조사마다 툴 서버를 stdio 자식 프로세스로 새로 띄우고, FDS_ALLOWED_TARGETS에 대상 거래 하나만 넣습니다.
모델이 다른 거래 ID를 인자로 넣어도 툴 서버가 OUT_OF_SCOPE로 거부합니다.
"""

import json
import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@dataclass(frozen=True)
class ToolCallResult:
    payload: dict[str, Any]   # structuredContent
    is_error: bool


class ToolSession(Protocol):
    tools: list[dict[str, Any]]   # Anthropic 형식: name, description, input_schema

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolCallResult: ...


class McpToolSession:
    """실제 MCP 툴 서버 세션. async with로 열고 닫습니다."""

    def __init__(self, target_transaction_id: str, db_path: Path, extra_env: dict[str, str] | None = None):
        self._target = target_transaction_id
        self._db_path = db_path
        self._extra_env = extra_env or {}
        self._stack = AsyncExitStack()
        self._session: ClientSession | None = None
        self.tools: list[dict[str, Any]] = []

    async def __aenter__(self) -> "McpToolSession":
        # 상속받은 환경에서 FDS_*, ANTHROPIC_* 를 지우고 필요한 것만 넣습니다(키가 툴 서버로 가지 않게).
        env = {k: v for k, v in os.environ.items() if not k.startswith(("FDS_", "ANTHROPIC_"))}
        env |= {"FDS_ALLOWED_TARGETS": self._target, "FDS_DB_PATH": str(self._db_path)} | self._extra_env
        params = StdioServerParameters(command=sys.executable, args=["-m", "fds_investigator.server"], env=env)
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        listed = await self._session.list_tools()
        self.tools = [{"name": t.name, "description": t.description or "", "input_schema": t.inputSchema}
                      for t in listed.tools]
        return self

    async def __aexit__(self, *exc) -> None:
        await self._stack.aclose()

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolCallResult:
        assert self._session is not None
        result = await self._session.call_tool(name, arguments)
        payload = result.structuredContent
        if payload is None:  # 계약상 항상 있어야 하지만, 없으면 텍스트를 보존해 기록합니다
            text = result.content[0].text if result.content else ""
            payload = {"status": "error", "error": {"code": "INTERNAL", "message": text[:200], "retryable": False}}
        return ToolCallResult(payload=json.loads(json.dumps(payload)), is_error=bool(result.isError))
