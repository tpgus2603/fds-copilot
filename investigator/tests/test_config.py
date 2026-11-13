"""허용 목록 fail closed (명세 2.1, T15)."""

import pytest

from fds_investigator.config import AllowList, Settings
from fds_investigator.errors import ConfigError


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"FDS_ALLOWED_TARGETS": ""},
        {"FDS_ALLOWED_TARGETS": "   "},
        {"FDS_ALLOWED_TARGETS": "abc,de;f"},
        {"FDS_ALLOWED_TARGETS": "abc,,def"},
        {"FDS_ALLOWED_TARGETS": "*"},
        {"FDS_ALLOWED_TARGETS": "*", "FDS_ENV": "prod"},
    ],
    ids=["missing", "empty", "blank", "bad-char", "empty-item", "wildcard-no-env", "wildcard-prod"],
)
def test_fail_closed(env):
    with pytest.raises(ConfigError):
        AllowList.from_env(env)


def test_wildcard_only_in_dev_or_test():
    assert AllowList.from_env({"FDS_ALLOWED_TARGETS": "*", "FDS_ENV": "test"}).permits("anything")
    assert AllowList.from_env({"FDS_ALLOWED_TARGETS": "*", "FDS_ENV": "dev"}).allow_all


def test_explicit_list():
    a = AllowList.from_env({"FDS_ALLOWED_TARGETS": " abc , DEF1 "})
    assert a.permits("abc") and a.permits("DEF1") and not a.permits("xyz")


def test_db_path_required():
    with pytest.raises(ConfigError):
        Settings.from_env({"FDS_ALLOWED_TARGETS": "abc"})
