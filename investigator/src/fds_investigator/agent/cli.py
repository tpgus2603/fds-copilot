"""조사 루프 CLI (#15).

사용: fds-investigate <대상 거래 ID> [--db 경로] [--model 모델명] [--out trace.json]

필요한 환경 변수: ANTHROPIC_API_KEY (SDK가 직접 읽음), CLAUDE_MODEL (--model로 대체 가능), FDS_DB_PATH (--db로 대체 가능)
키는 프로세스 환경 변수로만 전달하며, 툴 서버 프로세스와 실행 기록에는 전달·기록하지 않습니다.
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from .loop import Limits, investigate
from .model_client import AnthropicModelClient
from .tool_session import McpToolSession


def main() -> None:
    p = argparse.ArgumentParser(prog="fds-investigate", description="거래 한 건을 조사해 보고서를 만듭니다.")
    p.add_argument("target_transaction_id")
    p.add_argument("--db", default=os.environ.get("FDS_DB_PATH"))
    p.add_argument("--model", default=os.environ.get("CLAUDE_MODEL"))
    p.add_argument("--out", type=Path, help="실행 기록(JSON) 저장 경로")
    a = p.parse_args()
    missing = [n for n, v in [("--db/FDS_DB_PATH", a.db), ("--model/CLAUDE_MODEL", a.model),
                              ("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY"))] if not v]
    if missing:
        print(f"[fds-investigate] 필요한 설정이 없습니다: {', '.join(missing)}", file=sys.stderr)
        sys.exit(2)

    result = asyncio.run(investigate(
        a.target_transaction_id,
        AnthropicModelClient(),
        lambda target: McpToolSession(target, Path(a.db)),
        a.model,
        Limits(),
    ))
    data = result.to_dict()
    if a.out:
        a.out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"run_id": data["run_id"], "outcome": data["outcome"], "usage": data["usage"],
               "rendered": data["rendered"]}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    sys.exit(0 if result.outcome.status == "reported" else 1)


if __name__ == "__main__":
    main()
