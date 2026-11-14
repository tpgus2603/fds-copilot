"""조사 루프 CLI (#15, #28).

사용:
  fds-investigate <대상 거래 ID> [--provider anthropic|openai] [--model 모델명] [--db 경로] [--out trace.json]

  Claude API (기본):   ANTHROPIC_API_KEY, CLAUDE_MODEL 필요. 상한 프로필 cloud
  로컬 모델 (openai):  --base-url (기본 http://localhost:8080/v1, llama-server). 상한 프로필 local

키는 프로세스 환경 변수로만 전달하며, 툴 서버 프로세스와 실행 기록에는 전달·기록하지 않습니다.
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from .loop import PROFILES, investigate
from .model_client import AnthropicModelClient
from .openai_client import OpenAICompatibleModelClient
from .tool_session import McpToolSession


def main() -> None:
    p = argparse.ArgumentParser(prog="fds-investigate", description="거래 한 건을 조사해 보고서를 만듭니다.")
    p.add_argument("target_transaction_id")
    p.add_argument("--provider", choices=["anthropic", "openai"], default=os.environ.get("FDS_MODEL_PROVIDER", "anthropic"))
    p.add_argument("--base-url", default=os.environ.get("FDS_MODEL_BASE_URL", "http://localhost:8080/v1"))
    p.add_argument("--model", default=os.environ.get("CLAUDE_MODEL") or os.environ.get("FDS_MODEL"))
    p.add_argument("--profile", choices=sorted(PROFILES), help="상한 프로필 (기본: anthropic=cloud, openai=local)")
    p.add_argument("--thinking", action="store_true", help="로컬 모델의 생각 모드를 켭니다 (기본 끔)")
    p.add_argument("--token-count", choices=["llama_cpp", "estimate"], default="llama_cpp",
                   help="로컬 모델의 토큰 산정 방식 (llama-server가 아니면 estimate)")
    p.add_argument("--db", default=os.environ.get("FDS_DB_PATH"))
    p.add_argument("--out", type=Path, help="실행 기록(JSON) 저장 경로")
    a = p.parse_args()

    required = [("--db/FDS_DB_PATH", a.db), ("--model/CLAUDE_MODEL", a.model)]
    if a.provider == "anthropic":
        required.append(("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY")))
    missing = [n for n, v in required if not v]
    if missing:
        print(f"[fds-investigate] 필요한 설정이 없습니다: {', '.join(missing)}", file=sys.stderr)
        sys.exit(2)

    if a.provider == "anthropic":
        client = AnthropicModelClient()
        profile = a.profile or "cloud"
    else:
        client = OpenAICompatibleModelClient(a.base_url, disable_thinking=not a.thinking, token_count=a.token_count,
                                             api_key=os.environ.get("FDS_MODEL_API_KEY"))
        profile = a.profile or "local"

    result = asyncio.run(investigate(
        a.target_transaction_id, client, lambda target: McpToolSession(target, Path(a.db)), a.model, PROFILES[profile]))
    data = result.to_dict()
    data["provider"], data["profile"] = a.provider, profile
    if a.out:
        a.out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    seconds = sum(s.get("duration_ms", 0) for s in data["steps"]) / 1000
    summary = {"run_id": data["run_id"], "provider": a.provider, "model": a.model, "profile": profile,
               "outcome": data["outcome"], "usage": data["usage"], "elapsed_seconds_approx": round(seconds, 1),
               "rendered": data["rendered"]}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    sys.exit(0 if result.outcome.status == "reported" else 1)


if __name__ == "__main__":
    main()
