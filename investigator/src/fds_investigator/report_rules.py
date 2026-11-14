"""보고서 검증 규칙 중 언어·런타임에 따라 결과가 달라질 수 있는 부분을 고정합니다.

docs/04_agent_workflow.md 4.3의 V1(숫자 문자 금지)과 V4(인용 값 비교)의 기준 구현입니다.
JSON Schema 정규식은 ASCII 숫자만 막을 수 있으므로(유니코드 범주 \\p{N}의 지원이 구현마다 다름),
최종 판정은 이 모듈이 합니다. #15의 보고서 검증기는 이 함수들을 사용합니다.
"""

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any

PLACEHOLDER = re.compile(r"\{[0-9]\}")


def numeric_chars(text: str, *, allow_placeholders: bool) -> list[str]:
    """텍스트에 남은 숫자 문자 목록을 돌려줍니다. 비어 있어야 통과입니다.

    유니코드 범주가 N으로 시작하는 문자(Nd 십진 숫자, Nl 문자형 숫자, No 기타 숫자)를 모두 숫자로 봅니다.
    예: ASCII 0-9, 전각 ０-９, 아랍·페르시아 숫자, ⑤, Ⅻ, ½.
    allow_placeholders=True이면 ASCII 자리표시자 {0}~{9}만 먼저 제거합니다(발견 사항 문장).
    요약에는 자리표시자를 허용하지 않으므로 False로 호출합니다.
    """
    stripped = PLACEHOLDER.sub("", text) if allow_placeholders else text
    return [c for c in stripped if unicodedata.category(c).startswith("N")]


def json_type(value: Any) -> str:
    """JSON 관점의 타입. Python에서 bool은 int의 하위 타입이므로 먼저 구분합니다."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "other"


def json_values_equal(cited: Any, actual: Any) -> bool:
    """V4: 인용 값과 실제 값의 비교. JSON 타입이 같아야 하고, 숫자는 수치로 비교합니다.

    - false와 0, true와 1은 다릅니다 (Python의 False == 0을 따르지 않음).
    - 숫자끼리는 10과 10.0을 같게 봅니다 (10진수로 변환해 비교, 부동소수점 표현 차이 배제).
    - 금액처럼 문자열인 값은 문자열로 정확히 비교합니다 ("30.00" != "30.0").
    """
    t = json_type(cited)
    if t != json_type(actual) or t == "other":
        return False
    if t == "number":
        try:
            return Decimal(repr(cited)) == Decimal(repr(actual))
        except InvalidOperation:
            return False
    return cited == actual
