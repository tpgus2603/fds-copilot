"""툴 서버 설정. 명세 2.1: 허용 목록은 기본적으로 닫혀 있습니다(fail closed)."""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError

ID_PATTERN = re.compile(r"^[A-Za-z0-9]{1,64}$")
WILDCARD_ENVS = frozenset({"dev", "test"})


@dataclass(frozen=True)
class AllowList:
    ids: frozenset[str]
    allow_all: bool = False

    def permits(self, transaction_id: str) -> bool:
        return self.allow_all or transaction_id in self.ids

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "AllowList":
        raw = env.get("FDS_ALLOWED_TARGETS")
        if raw is None or not raw.strip():
            raise ConfigError("FDS_ALLOWED_TARGETS가 설정되지 않았거나 비어 있습니다.")
        value = raw.strip()
        if value == "*":
            if env.get("FDS_ENV") not in WILDCARD_ENVS:
                raise ConfigError("FDS_ALLOWED_TARGETS='*'는 FDS_ENV=dev 또는 test에서만 허용됩니다.")
            return cls(ids=frozenset(), allow_all=True)
        items = [item.strip() for item in value.split(",")]
        invalid = [item for item in items if not ID_PATTERN.fullmatch(item)]
        if invalid:
            raise ConfigError(f"FDS_ALLOWED_TARGETS에 형식이 잘못된 항목이 {len(invalid)}개 있습니다.")
        return cls(ids=frozenset(items))


@dataclass(frozen=True)
class Settings:
    allowlist: AllowList
    db_path: Path
    query_timeout_seconds: float = 5.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        allowlist = AllowList.from_env(env)
        db = env.get("FDS_DB_PATH")
        if not db or not db.strip():
            raise ConfigError("FDS_DB_PATH가 설정되지 않았습니다.")
        timeout = float(env.get("FDS_QUERY_TIMEOUT_SECONDS", "5"))
        return cls(allowlist=allowlist, db_path=Path(db), query_timeout_seconds=timeout)
