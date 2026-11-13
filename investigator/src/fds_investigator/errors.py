"""툴 실행 오류와 설정 오류."""

from enum import StrEnum


class ErrorCode(StrEnum):
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    NOT_FOUND = "NOT_FOUND"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    DATA_SOURCE_UNAVAILABLE = "DATA_SOURCE_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    INTERNAL = "INTERNAL"


# 명세 3.8: retryable은 오류 코드로 결정됩니다. 일시적 장애만 true입니다.
RETRYABLE = frozenset({ErrorCode.DATA_SOURCE_UNAVAILABLE, ErrorCode.TIMEOUT})


class ConfigError(Exception):
    """잘못된 설정. 서버는 시작을 거부합니다(fail closed)."""


class DataSourceUnavailable(Exception):
    """저장소에 연결하거나 읽을 수 없음."""
