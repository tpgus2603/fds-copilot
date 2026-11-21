"""보고서 검증 V1~V10 (docs/04 4.3). 하나라도 실패하면 보고서를 폐기하고 미완료로 끝냅니다.

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


# V9: 값의 방향이 정해진 필드. (경로, 값) → 그 값이 뒷받침하는 kind
_POLARITY: dict[str, dict[Any, str]] = {
    "/stats/target_exceeds_max": {True: "suspicion", False: "counter"},
    "/target_category/seen_before": {False: "suspicion", True: "counter"},
}
_CATEGORY_SHARE = re.compile(r"^/category_distribution/[0-9]{1,2}/category$")
# V10: evidence_id = EV-<툴>-<대상>-<기간>d
_EVIDENCE_ID = re.compile(r"^(EV-[A-Z]{2}-[A-Za-z0-9]{1,64})-([0-9]{1,3})d$")


# V9 보강 (PR #31 리뷰): 비교 이력이 없거나(empty) 최소 표본 미만(insufficient)이면, 이력에서 나온 값은
# 방향(의심/정상)을 정하지 못합니다. 예: 이력이 비어 seen_before=false인 것은 "처음 쓰는 업종"이 아니라
# "확인할 이력이 없음"입니다. 이런 결과의 이력 기반 필드는 gap에서만 인용할 수 있습니다.
SPARSE_STATUSES = ("empty", "insufficient")
HISTORY_FIELDS = ("/stats/", "/target_category/", "/category_distribution/", "/recent_transactions/")


def _sparse_history_direction(kind: str, path: str, snap: dict) -> str | None:
    if kind in ("suspicion", "counter") and snap.get("status") in SPARSE_STATUSES and path.startswith(HISTORY_FIELDS):
        return f"비교 이력이 {snap['status']}인 결과의 {path}는 {kind}의 근거가 될 수 없습니다(gap으로만 인용)"
    return None


def _polarity_conflict(kind: str, path: str, value: Any, snap: dict) -> str | None:
    """V9 (휴리스틱): 인용한 값의 방향이 발견 사항의 kind와 반대이면 그 이유를 돌려줍니다. gap은 검사하지 않습니다."""
    if kind not in ("suspicion", "counter"):
        return None
    table = _POLARITY.get(path)
    if table is not None and isinstance(value, bool) and table[value] != kind:
        return f"{path}={json.dumps(value)} 는 {table[value]}의 근거입니다"
    if kind == "counter" and _CATEGORY_SHARE.match(path):
        target = resolve_pointer(snap, "/target/category")
        if target is not _MISSING and value != target:
            return f"대상 업종({target})과 다른 업종({value})은 정상 정황의 근거가 아닙니다"
    return None


MAX_SCHEMA_FAILURES = 10
# 대표 코드 우선순위 (V1~V7을 앞에 두어 기존 지표와 비교 가능하게 유지)
FAILURE_CODES = ["REPORT_SCHEMA_INVALID", "EVIDENCE_NOT_FOUND", "EVIDENCE_PATH_INVALID", "EVIDENCE_VALUE_MISMATCH",
                 "PLACEHOLDER_INVALID", "ERROR_RESULT_CITED", "NUMBER_WORD_USED",
                 "EVIDENCE_NOT_REFERENCED", "MIXED_LOOKBACK", "POLARITY_CONFLICT", "SPARSE_HISTORY_DIRECTION"]


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


def _is_text_pattern_error(e: Any) -> bool:
    """요약·문장 문자열의 pattern 위반만 참. 이런 위반은 보고서 구조를 깨지 않습니다."""
    path = list(e.path)
    is_text = path == ["summary"] or (len(path) == 3 and path[0] == "findings" and path[2] == "statement")
    return e.validator == "pattern" and is_text


def verify_report(report: Any, snapshots: dict[str, dict]) -> VerificationResult:
    failures: list[dict[str, str]] = []

    def fail(code: str, where: str, detail: str) -> None:
        failures.append({"code": code, "where": where, "detail": detail})

    # V1: 스키마 + 숫자 문자 금지 (유니코드 숫자 범주 전체)
    errors = list(Draft202012Validator(report_schema()).iter_errors(report))
    # 검사를 멈추게 하는 구조 오류를 앞에 둡니다. 상한을 넘으면 생략 수를 남겨, 한 번뿐인 수정 요청에서
    # 실제 중단 원인이 잘려 나가지 않게 합니다 (PR #39 리뷰).
    structural = [e for e in errors if not _is_text_pattern_error(e)]
    ordered = structural + [e for e in errors if _is_text_pattern_error(e)]
    for e in ordered[:MAX_SCHEMA_FAILURES]:
        fail("REPORT_SCHEMA_INVALID", "/" + "/".join(map(str, e.path)), e.message[:200])
    if len(ordered) > MAX_SCHEMA_FAILURES:
        fail("REPORT_SCHEMA_INVALID", "", f"그 밖의 스키마 위반 {len(ordered) - MAX_SCHEMA_FAILURES}건 생략")
    # 문장·요약의 문자 패턴 위반(숫자·자리표시자)은 구조 오류가 아니므로 V2~V10을 계속 검사해 실패를 모두 모읍니다.
    # 첫 실패에서 멈추면 수정 요청(4.4)이 일부 위치만 알려 주게 되고, 모델이 그것만 고친 뒤 다음 검증에서
    # 처음부터 있던 다른 실패로 다시 떨어집니다(27B r1 관찰). 구조 오류는 이후 검사가 전제를 잃으므로 멈춥니다.
    if structural:
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
        used = {int(n) for n in PLACEHOLDER.findall(f["statement"])}
        for n in sorted(used):
            if n >= len(f["evidence"]):
                fail("PLACEHOLDER_INVALID", f"/findings/{i}/statement", f"{{{n}}}")
        # V8: 인용한 근거는 모두 문장에 드러나야 함 (조사역이 어떤 값이 근거인지 문장에서 확인)
        for j in range(len(f["evidence"])):
            if j not in used:
                fail("EVIDENCE_NOT_REFERENCED", f"/findings/{i}/evidence/{j}", f"문장에 {{{j}}}가 없습니다")
        # V9
        for j, ref in enumerate(f["evidence"]):
            snap = snapshots.get(ref["evidence_id"])
            if snap is None:
                continue
            if why := _sparse_history_direction(f["kind"], ref["path"], snap):
                fail("SPARSE_HISTORY_DIRECTION", f"/findings/{i}/evidence/{j}", why)
            elif why := _polarity_conflict(f["kind"], ref["path"], ref["value"], snap):
                fail("POLARITY_CONFLICT", f"/findings/{i}/evidence/{j}", why)
        # V10: 같은 툴·대상의 서로 다른 조회 기간을 한 발견 사항에 섞지 않음
        periods: dict[str, set[str]] = {}
        for ref in f["evidence"]:
            if m := _EVIDENCE_ID.match(ref["evidence_id"]):
                periods.setdefault(m.group(1), set()).add(m.group(2))
        for source, days in periods.items():
            if len(days) > 1:
                fail("MIXED_LOOKBACK", f"/findings/{i}", f"{source}: {sorted(days, key=int)}일")

    # V7
    texts = [("/summary", report["summary"])] + [(f"/findings/{i}/statement", f["statement"])
                                                 for i, f in enumerate(report["findings"])]
    for where, text in texts:
        if m := _NUMBER_WORDS.search(text):
            fail("NUMBER_WORD_USED", where, m.group(0))

    if not failures:
        return VerificationResult(True)
    first = min(failures, key=lambda x: FAILURE_CODES.index(x["code"]))
    return VerificationResult(False, first["code"], failures)
