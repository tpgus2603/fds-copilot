"""조사 루프 CLI (#15, #28).

사용:
  fds-investigate <대상 거래 ID> [--provider anthropic|openai] [--model 모델명] [--db 경로] [--out trace.json]

  Claude API (기본):   ANTHROPIC_API_KEY, CLAUDE_MODEL 필요. 상한 프로필 cloud
  로컬 모델 (openai):  --base-url (기본 http://localhost:8080/v1, llama-server). 상한 프로필 local

키는 프로세스 환경 변수로만 전달하며, 툴 서버 프로세스와 실행 기록에는 전달·기록하지 않습니다.
"""

import argparse
import asyncio
import dataclasses
import json
import os
import sys
from pathlib import Path

from .loop import PROFILES, investigate
from .model_client import AnthropicModelClient
from .openai_client import BACKENDS, SAMPLING_PRESETS, OpenAICompatibleModelClient
from .tool_session import McpToolSession


def main() -> None:
    p = argparse.ArgumentParser(prog="fds-investigate", description="거래 한 건을 조사해 보고서를 만듭니다.")
    p.add_argument("target_transaction_id")
    p.add_argument("--provider", choices=["anthropic", "openai"], default=os.environ.get("FDS_MODEL_PROVIDER", "anthropic"))
    p.add_argument("--base-url", default=os.environ.get("FDS_MODEL_BASE_URL", "http://localhost:8080/v1"))
    p.add_argument("--model", default=os.environ.get("CLAUDE_MODEL") or os.environ.get("FDS_MODEL"))
    p.add_argument("--profile", choices=sorted(PROFILES), help="상한 프로필 (기본: anthropic=cloud, openai=local)")
    p.add_argument("--thinking", action="store_true", help="로컬 모델의 생각 모드를 켭니다 (기본 끔)")
    p.add_argument("--backend", choices=sorted(BACKENDS), default=os.environ.get("FDS_MODEL_BACKEND", "llama_cpp"),
                   help="OpenAI 호환 서버 종류. 생각 모드 끄기 필드와 기본 토큰 산정 방식을 정합니다")
    p.add_argument("--token-count", choices=["llama_cpp", "estimate"],
                   help="토큰 산정 방식 (기본: llama_cpp 백엔드는 서버 토크나이저, 그 밖은 estimate 최선 노력 추정)")
    p.add_argument("--sampling", choices=sorted(SAMPLING_PRESETS), default=os.environ.get("FDS_SAMPLING"),
                   help="샘플링 프리셋 (기본: llama_cpp 백엔드는 qwen-instruct, 그 밖은 server = 아무것도 보내지 않음)")
    p.add_argument("--context-window", type=int,
                   help="요청당 컨텍스트 창 (토큰). 서버의 컨텍스트 길이(llama-server -c, Ollama num_ctx)와 같게 맞춥니다")
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
        extra = {"token_count": a.token_count} if a.token_count else {}
        client = OpenAICompatibleModelClient.for_backend(a.backend, a.base_url, disable_thinking=not a.thinking, sampling=a.sampling,
                                                         api_key=os.environ.get("FDS_MODEL_API_KEY"), **extra)
        profile = a.profile or "local"

    limits = PROFILES[profile]
    if a.context_window is not None:
        if a.context_window <= limits.max_tokens:
            print(f"[fds-investigate] --context-window는 max_tokens({limits.max_tokens})보다 커야 합니다", file=sys.stderr)
            sys.exit(2)
        limits = dataclasses.replace(limits, context_window=a.context_window)

    result = asyncio.run(investigate(
        a.target_transaction_id, client, lambda target: McpToolSession(target, Path(a.db)), a.model, limits))
    data = result.to_dict()
    data["provider"], data["profile"] = a.provider, profile
    if a.provider == "openai":
        data["backend"], data["token_count"] = a.backend, client.token_count
        data["sampling"] = {"preset": client.sampling, **SAMPLING_PRESETS[client.sampling],
                            "parallel_tool_calls": client.parallel_tool_calls}
        data["context_window"] = limits.context_window
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
