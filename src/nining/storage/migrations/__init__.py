"""Ordered SQLite migrations bundled as Python resources.

Keeping SQL in importable modules ensures migrations are present in wheels even
when a packaging frontend does not include arbitrary ``.sql`` files.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from .v0001_initial import SQL as V0001_SQL


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return sha256(self.sql.encode("utf-8")).hexdigest()


MIGRATIONS = (Migration(1, "initial_game_state", V0001_SQL),)
