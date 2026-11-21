"""시스템 프롬프트의 계약 설명이 실제 렌더링·카탈로그와 일치하는지 고정합니다 (#34)."""

import re

from fds_investigator.agent.prompts import (
    BAD_EXAMPLE,
    EXAMPLE_EVIDENCE,
    GOOD_EXAMPLE,
    SYSTEM_PROMPT,
    citable_paths,
)
from fds_investigator.agent.render import renderer_for


def test_every_example_path_is_citable():
    for path, value in EXAMPLE_EVIDENCE:
        assert renderer_for(path) is not None, path


def test_prompt_shows_actual_rendered_phrases():
    for path, value in EXAMPLE_EVIDENCE:
        assert f"[{renderer_for(path)(value)}]" in SYSTEM_PROMPT, path


def test_good_and_bad_examples_render_as_described():
    assert "[비교 기간 평균 대비 10.0배]이며 [비교 기간 최댓값 초과]입니다." in SYSTEM_PROMPT
    # 나쁜 예는 실제로 단위가 겹친 결과를 보여 줘야 의미가 있음
    assert "[비교 기간 평균 대비 10.0배]배" in SYSTEM_PROMPT
    assert "{0}" in GOOD_EXAMPLE[0] and "{0}배" in BAD_EXAMPLE[0]


def test_citable_path_list_matches_catalog_and_excludes_invented_paths():
    listed = citable_paths()
    assert ", ".join(listed) in SYSTEM_PROMPT
    for path in ("/stats/txn_count", "/category_distribution/0/category", "/recent_transactions/3/amount"):
        assert renderer_for(path) is not None
        assert any(re.fullmatch(p.replace("N", "[0-9]{1,2}"), path) for p in listed), path
    for invented in ("/recent_transactions/length", "/category_distribution", "/status"):
        assert renderer_for(invented) is None
        assert invented not in listed


def test_sample_size_rule_has_gap_example():
    assert "표본 수가 적다는 사실은 counter가 아니라 gap입니다. 예:" in SYSTEM_PROMPT
