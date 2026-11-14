"""보고서 검증 V1~V7 (docs/04 4.3). 하나라도 실패하면 보고서를 폐기하고 미완료로 끝냅니다.

검증 기준은 이번 실행에서 실제로 받은 툴 결과 스냅샷(evidence_id → 결과)입니다.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from ..report_rules import json_values_equal, numeric_chars
from .render import PLACEHOLDER, renderer_for

_SCHEMA_PATH = Path(__file__).resolve().parents[4] / "contracts" / "agent" / "report.schema.json"
_MISSING = object()

# V7: 한글 수량 표현 (보조 휴리스틱). 목록에 없는 표현은 놓칠 수 있습니다.
_NUMBER_WORDS = re.compile(
    r"(한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무|서른|마흔|쉰|백|천|만)\s*(배|건|번|개|일|달|시간|분|초|퍼센트|프로)"
    r"|절반|반절|수십|수백|수천|수만|몇\s*배"
)


def report_schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


@dataclass
class VerificationResult:
    ok: bool
    code: str | None = None          # 첫 번째 실패의 사유 코드
    failures: list[dict[str, str]] = field(default_factory=list)


def resolve_pointer(doc: Any, pointer: str) -> Any:
    """JSON Pointer(RFC 6901). 경로가 없으면 _MISSING."""
    cur = doc
    for raw in pointer.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, dict):
            if token not in cur:
                return _MISSING
            cur = cur[token]
        elif isinstance(cur, list):
            if not token.isdigit() or int(token) >= len(cur):
                return _MISSING
            cur = cur[int(token)]
        else:
            return _MISSING
    return cur


def verify_report(report: Any, snapshots: dict[str, dict]) -> VerificationResult:
    failures: list[dict[str, str]] = []

    def fail(code: str, where: str, detail: str) -> None:
        failures.append({"code": code, "where": where, "detail": detail})

    # V1: 스키마 + 숫자 문자 금지 (유니코드 숫자 범주 전체)
    errors = list(Draft202012Validator(report_schema()).iter_errors(report))
    if errors:
        for e in errors[:5]:
            fail("REPORT_SCHEMA_INVALID", "/" + "/".join(map(str, e.path)), e.message[:200])
        return VerificationResult(False, "REPORT_SCHEMA_INVALID", failures)
    if chars := numeric_chars(report["summary"], allow_placeholders=False):
        fail("REPORT_SCHEMA_INVALID", "/summary", f"숫자 문자 {chars[:3]}")
    for i, f in enumerate(report["findings"]):
        if chars := numeric_chars(f["statement"], allow_placeholders=True):
            fail("REPORT_SCHEMA_INVALID", f"/findings/{i}/statement", f"숫자 문자 {chars[:3]}")

    for i, f in enumerate(report["findings"]):
        for j, ref in enumerate(f["evidence"]):
            where = f"/findings/{i}/evidence/{j}"
            snap = snapshots.get(ref["evidence_id"])
            # V2
            if snap is None:
                fail("EVIDENCE_NOT_FOUND", where, ref["evidence_id"])
                continue
            # V6
            if snap.get("status") == "error" and f["kind"] in ("suspicion", "counter"):
                fail("ERROR_RESULT_CITED", where, ref["evidence_id"])
                continue
            # V3
            actual = resolve_pointer(snap, ref["path"])
            if actual is _MISSING or renderer_for(ref["path"]) is None:
                fail("EVIDENCE_PATH_INVALID", where, ref["path"])
                continue
            # V4: JSON 타입 먼저, 숫자는 10진수 비교
            if not json_values_equal(ref["value"], actual):
                fail("EVIDENCE_VALUE_MISMATCH", where, f"cited={ref['value']!r} actual={actual!r}")
        # V5
        for n in PLACEHOLDER.findall(f["statement"]):
            if int(n) >= len(f["evidence"]):
                fail("PLACEHOLDER_INVALID", f"/findings/{i}/statement", f"{{{n}}}")

    # V7
    texts = [("/summary", report["summary"])] + [(f"/findings/{i}/statement", f["statement"])
                                                 for i, f in enumerate(report["findings"])]
    for where, text in texts:
        if m := _NUMBER_WORDS.search(text):
            fail("NUMBER_WORD_USED", where, m.group(0))

    if not failures:
        return VerificationResult(True)
    order = ["REPORT_SCHEMA_INVALID", "EVIDENCE_NOT_FOUND", "EVIDENCE_PATH_INVALID", "EVIDENCE_VALUE_MISMATCH",
             "PLACEHOLDER_INVALID", "ERROR_RESULT_CITED", "NUMBER_WORD_USED"]
    first = min(failures, key=lambda x: order.index(x["code"]))
    return VerificationResult(False, first["code"], failures)
