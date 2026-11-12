# contracts

계층 간 계약(스키마)을 둡니다. 두 언어(Java, Python)가 공유하는 형식은 이 폴더가 기준입니다.

| 경로 | 내용 | 문서 |
|---|---|---|
| `mcp/*.input.schema.json` | MCP 툴 입력 (`inputSchema`로 그대로 사용) | `docs/03_mcp_tool_spec.md` |
| `mcp/*.output.schema.json` | MCP 툴 결과 (`structuredContent`) | `docs/03_mcp_tool_spec.md` |

- JSON Schema draft 2020-12를 사용합니다.
- 스키마를 바꾸면 `schema_version`을 올리고, 관련 문서와 구현을 같은 PR에서 수정합니다.
