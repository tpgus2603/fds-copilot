"""MCP 툴 서버 (stdio).

명세 2.2 구현 요구사항:
- tools/list에 계약 입력·출력 스키마를 그대로 노출합니다.
- SDK 입력 검증을 끄고(validate_input=False) 핸들러에서 계약 스키마로 검증합니다.
- CallToolResult를 직접 반환해 isError와 structuredContent를 함께 설정합니다.
- 반환 전에 출력 스키마로 자체 검증하고, 위반이면 INTERNAL 오류로 대체합니다.
"""

import asyncio
import json
import logging
import sys

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from . import contracts
from .config import Settings
from .errors import ConfigError, ErrorCode
from .repository import SqliteTransactionRepository, TransactionRepository
from .service import error_result, get_transaction_history

log = logging.getLogger("fds_investigator.server")

DESCRIPTION = (
    "대상 거래의 금액이나 업종이 이 카드의 평소 이용 패턴과 다른지 확인할 때 사용합니다.\n"
    "대상 거래 시점 이전의 비교 기간(기본 90일, lookback_days로 7~365일 지정) 거래를 집계해\n"
    "금액 통계(건수, 평균, 중앙값, 최대값, 20건 이상일 때만 95백분위), 대상 금액이 평균의 몇 배인지,\n"
    "업종 분포, 선택한 비교 기간 안에서 대상 업종을 이용한 적이 있는지, 최근 거래 최대 10건을 돌려줍니다.\n"
    "대상 거래 자체와 그 이후 거래는 포함되지 않으므로, 비교 기간 밖의 이용 이력은 알 수 없습니다.\n"
    "status가 insufficient이면 표본이 4건 이하라 통계를 단정적으로 해석하면 안 되고,\n"
    "empty이면 비교 기간에 거래가 없다는 뜻이며, error이면 근거를 확보하지 못한 것입니다.\n"
    "결과의 수치를 인용할 때는 evidence_id를 함께 적으십시오."
)


def to_call_result(result: dict) -> types.CallToolResult:
    """출력 계약을 검증한 뒤 MCP 결과로 변환합니다. content는 structuredContent의 직렬화입니다(명세 3.9)."""
    violations = list(contracts.output_validator().iter_errors(result))
    if violations:
        log.error("output contract violation: %s", [v.message for v in violations[:3]])
        result = error_result(ErrorCode.INTERNAL)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structuredContent=result,
        isError=result["status"] == "error",
    )


def build_server(settings: Settings, repo: TransactionRepository) -> Server:
    server: Server = Server("fds-evidence-tools")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=contracts.TOOL_NAME,
                description=DESCRIPTION,
                inputSchema=contracts.load_schema("input"),
                outputSchema=contracts.load_schema("output"),
            )
        ]

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict | None) -> types.CallToolResult:
        if name != contracts.TOOL_NAME:
            # 존재하지 않는 툴: 툴 실행 오류가 아니라 호출 측 오류이므로 예외로 알립니다.
            raise ValueError(f"Unknown tool: {name}")
        try:
            result = await get_transaction_history(
                arguments, settings.allowlist, repo, settings.query_timeout_seconds
            )
        except Exception:
            log.exception("unhandled error in tool handler")
            result = error_result(ErrorCode.INTERNAL)
        return to_call_result(result)

    return server


async def serve(settings: Settings, repo: TransactionRepository) -> None:
    server = build_server(settings, repo)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    try:
        settings = Settings.from_env()
    except ConfigError as e:
        print(f"[fds-evidence-tools] 시작 거부: {e}", file=sys.stderr)
        sys.exit(1)
    repo = SqliteTransactionRepository(settings.db_path)
    asyncio.run(serve(settings, repo))


if __name__ == "__main__":
    main()
