"""보고서 검증 규칙 V1(숫자 문자)과 V4(값 비교)의 기준 동작. PR #23 재리뷰 사례를 고정합니다."""

import pytest

from fds_investigator.report_rules import json_values_equal, numeric_chars


@pytest.mark.parametrize(
    "text",
    ["기준 금액은 ５００달러입니다.", "기준 금액은 ۵۰۰달러입니다.", "기준 금액은 ⑤달러입니다.",
     "제Ⅻ조", "금액의 ½", "٣ 건", "평균 결제 금액은 5달러입니다.", "2020-01-01 02:13"],
    ids=["fullwidth", "persian", "circled", "roman", "fraction", "arabic-indic", "ascii", "datetime"],
)
def test_numeric_chars_rejected_in_statement(text):
    assert numeric_chars(text, allow_placeholders=True)


def test_placeholders_allowed_only_in_statements():
    assert numeric_chars("대상 금액이 {0}이며 {1}입니다.", allow_placeholders=True) == []
    assert numeric_chars("대상 금액이 {0}입니다.", allow_placeholders=False) == ["0"]  # 요약에서는 금지


def test_fullwidth_placeholder_is_not_a_placeholder():
    assert numeric_chars("대상 금액이 {０}입니다.", allow_placeholders=True) == ["０"]


def test_korean_number_words_are_not_numeric_chars():
    # 한글 수량 표현은 문자 검사가 아니라 V7 휴리스틱의 대상입니다.
    assert numeric_chars("평균의 두 배이며 절반 이상", allow_placeholders=True) == []


@pytest.mark.parametrize(
    "cited,actual,expected",
    [
        (False, 0, False), (True, 1, False), (0, False, False), (1, True, False),   # 불리언 ≠ 숫자
        (10, 10.0, True), (10.0, 10, True), (500, 500.0, True), (0.1, 0.1, True),  # 숫자끼리 수치 비교
        (10, 10.01, False),
        ("30.00", "30.00", True), ("30.0", "30.00", False), (30.0, "30.00", False),  # 금액 문자열은 정확히
        (None, None, True), (None, False, False), ("false", False, False),
        (False, False, True), (True, True, True),
    ],
)
def test_json_values_equal(cited, actual, expected):
    assert json_values_equal(cited, actual) is expected
