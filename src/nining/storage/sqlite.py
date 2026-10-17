"""SQLite index backend backed only by Python's standard library."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .common import (
    MigrationError,
    StorageBusyError,
    StorageConflictError,
    StorageCorruptError,
    StorageError,
    StorageReadOnlyError,
    canonical_dumps,
    normalize_utc,
)
from .memory import ALL_COLLECTIONS, MemoryStorage
from .migrations import MIGRATIONS


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def apply_migrations(
    connection: sqlite3.Connection, *, read_only: bool = False
) -> None:
    """Verify checksums and apply all bundled migrations transactionally."""

    if not read_only:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                sha256 TEXT NOT NULL,
                applied_at_utc TEXT NOT NULL
            )
            """
        )
        connection.commit()
    try:
        rows = connection.execute(
            "SELECT version, name, sha256 FROM schema_migrations ORDER BY version"
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        raise MigrationError("schema_migrations is missing or unreadable") from exc
    applied = {int(row[0]): (str(row[1]), str(row[2])) for row in rows}
    known = {migration.version: migration for migration in MIGRATIONS}
    unknown = sorted(set(applied) - set(known))
    if unknown:
        raise MigrationError(f"database contains unknown future migrations: {unknown}")
    for version, (name, checksum) in applied.items():
        migration = known[version]
        if (name, checksum) != (migration.name, migration.checksum):
            raise MigrationError(f"migration checksum drift at version {version}")
    missing = [
        migration for migration in MIGRATIONS if migration.version not in applied
    ]
    if read_only and missing:
        raise MigrationError("read-only database is not fully migrated")
    for migration in missing:
        applied_at = normalize_utc(None)
        statement = (
            "BEGIN IMMEDIATE;\n"
            + migration.sql
            + "\nINSERT INTO schema_migrations(version,name,sha256,applied_at_utc) VALUES ("
            + f"{migration.version},{_sql_literal(migration.name)},"
            + f"{_sql_literal(migration.checksum)},{_sql_literal(applied_at)});\nCOMMIT;"
        )
        try:
            connection.executescript(statement)
        except sqlite3.DatabaseError as exc:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.DatabaseError:
                pass
            raise MigrationError(
                f"failed to apply migration {migration.version}"
            ) from exc


class SQLiteStorage(MemoryStorage):
    """SQLite implementation retaining the same semantic memory mirror."""

    backend_name = "sqlite"

    def __init__(
        self,
        path: str | Path,
        *,
        busy_timeout_ms: int = 5000,
        wal: bool = False,
        read_only: bool = False,
    ) -> None:
        super().__init__()
        self.path = str(path)
        self.read_only = bool(read_only)
        self.busy_timeout_ms = max(0, int(busy_timeout_ms))
        self.wal = bool(wal)
        self._loading = True
        try:
            if self.path == ":memory:":
                if self.read_only:
                    raise StorageReadOnlyError(":memory: cannot be opened read-only")
                target = self.path
                uri = False
            else:
                resolved = Path(path).expanduser().resolve()
                self.path = str(resolved)
                if not resolved.parent.exists() and not self.read_only:
                    resolved.parent.mkdir(parents=True, exist_ok=True)
                if self.read_only:
                    target = f"file:{quote(resolved.as_posix())}?mode=ro"
                    uri = True
                else:
                    target = str(resolved)
                    uri = False
            self._conn = sqlite3.connect(
                target,
                timeout=self.busy_timeout_ms / 1000,
                uri=uri,
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            if self.wal and not self.read_only and self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            apply_migrations(self._conn, read_only=self.read_only)
            snapshot = self._query_snapshot()
            self.import_snapshot(snapshot)
            self._loading = False
        except StorageError:
            connection = getattr(self, "_conn", None)
            if connection is not None:
                connection.close()
            self._closed = True
            raise
        except sqlite3.DatabaseError as exc:
            connection = getattr(self, "_conn", None)
            if connection is not None:
                connection.close()
            self._closed = True
            raise self._translate_sqlite(exc) from exc
        except OSError as exc:
            connection = getattr(self, "_conn", None)
            if connection is not None:
                connection.close()
            self._closed = True
            raise StorageError(f"cannot open SQLite storage: {exc}") from exc

    @staticmethod
    def _translate_sqlite(exc: sqlite3.DatabaseError) -> StorageError:
        message = str(exc).lower()
        if "locked" in message or "busy" in message:
            return StorageBusyError("SQLite storage is busy")
        if "readonly" in message or "read-only" in message:
            return StorageReadOnlyError("SQLite storage is read-only")
        if (
            "malformed" in message
            or "not a database" in message
            or "file is encrypted" in message
        ):
            return StorageCorruptError("SQLite database is corrupt or not a database")
        if isinstance(exc, sqlite3.IntegrityError):
            return StorageConflictError(f"SQLite constraint rejected record: {exc}")
        return StorageError(f"SQLite failure: {exc}")

    def _execute(self, sql: str, parameters: tuple[Any, ...]) -> None:
        if self.read_only:
            raise StorageReadOnlyError("SQLite storage was opened read-only")
        try:
            with self._conn:
                self._conn.execute(sql, parameters)
        except sqlite3.DatabaseError as exc:
            raise self._translate_sqlite(exc) from exc

    def _persist_append(
        self, collection: str, key: Any, record: Mapping[str, Any]
    ) -> None:
        if self._loading:
            return
        r = record
        if collection == "playthroughs":
            self._execute(
                "INSERT INTO playthroughs VALUES (?,?,?,?,?,?,?,?)",
                (
                    r["playthrough_id"],
                    r["title"],
                    r["materials_manifest_sha256"],
                    r["status"],
                    r["default_view_mode"],
                    canonical_dumps(r["metadata"]),
                    r["created_at_utc"],
                    r["updated_at_utc"],
                ),
            )
        elif collection == "branches":
            self._execute(
                "INSERT INTO branches VALUES (?,?,?,?,?,?,?)",
                (
                    r["branch_id"],
                    r["playthrough_id"],
                    r["name"],
                    r["parent_branch_id"],
                    r["forked_from_checkpoint_id"],
                    r["status"],
                    r["created_at_utc"],
                ),
            )
        elif collection == "runs":
            self._execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    r["run_id"],
                    r["playthrough_id"],
                    r["branch_id"],
                    r["created_at_utc"],
                    r["finished_at_utc"],
                    r["status"],
                    r["mode"],
                    r["requested_storage"],
                    r["effective_storage"],
                    r["fallback_reason"],
                    r["materials_manifest_sha256"],
                    r["config_sha256"],
                    r["code_version"],
                    canonical_dumps(r["capabilities"]),
                    r["safe_error"],
                ),
            )
        elif collection == "story_events":
            self._execute(
                "INSERT INTO story_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    r["story_event_id"],
                    r["playthrough_id"],
                    r["branch_id"],
                    r["run_id"],
                    r["trigger_id"],
                    r["sequence_no"],
                    r["event_type"],
                    r["actor"],
                    canonical_dumps(r["payload"]),
                    int(r["reversible"]),
                    r["parent_event_id"],
                    r["created_at_utc"],
                ),
            )
        elif collection == "checkpoints":
            self._execute(
                "INSERT INTO checkpoints VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    r["checkpoint_id"],
                    r["playthrough_id"],
                    r["branch_id"],
                    r["head_event_id"],
                    r["generated_by_run_id"],
                    r["label"],
                    r["checkpoint_kind"],
                    canonical_dumps(r["state"]),
                    r["state_sha256"],
                    r["created_at_utc"],
                ),
            )
        elif collection == "feature_selections":
            self._execute(
                "INSERT INTO feature_selections VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    r["selection_id"],
                    r["playthrough_id"],
                    r["branch_id"],
                    r["checkpoint_id"],
                    r["feature_key"],
                    r["option_key"],
                    canonical_dumps(r["config"]),
                    r["selected_by"],
                    int(r["effective"]),
                    r["created_at_utc"],
                ),
            )
        elif collection == "provider_calls":
            self._execute(
                "INSERT INTO provider_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    r["call_id"],
                    r["run_id"],
                    r["trigger_id"],
                    r["story_event_id"],
                    r["purpose"],
                    r["provider"],
                    r["model"],
                    r["mode"],
                    r["endpoint_alias"],
                    r["request_sha256"],
                    r["prompt_sha256"],
                    canonical_dumps(r["parameters"]),
                    r["started_at_utc"],
                    r["finished_at_utc"],
                    r["latency_ms"],
                    canonical_dumps(r["usage"]),
                    r["cost_amount_micros"],
                    r["cost_currency"],
                    r["billing_state"],
                    r["status"],
                    r["error_code"],
                    r["safe_error"],
                    r["retry_of_call_id"],
                ),
            )
        elif collection == "assets":
            self._execute(
                "INSERT INTO assets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    r["asset_id"],
                    r["run_id"],
                    r["trigger_id"],
                    r["provider_call_id"],
                    r["role"],
                    r["media_type"],
                    r["relative_path"],
                    r["mime_type"],
                    r["size_bytes"],
                    r["width_px"],
                    r["height_px"],
                    r["duration_ms"],
                    r["sha256"],
                    r["provenance"],
                    int(r["final_eligible"]),
                    r["created_at_utc"],
                ),
            )
        elif collection == "decisions":
            self._execute(
                "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    r["decision_id"],
                    r["run_id"],
                    r["trigger_id"],
                    r["subject_kind"],
                    r["subject_id"],
                    r["action"],
                    r["reason"],
                    r["decided_by"],
                    r["supersedes_decision_id"],
                    r["created_at_utc"],
                ),
            )
        else:  # pragma: no cover - guards future collection additions
            raise StorageError(
                f"SQLite persistence is missing collection: {collection}"
            )

    def _persist_mutable(
        self, collection: str, key: Any, record: Mapping[str, Any]
    ) -> None:
        if self._loading:
            return
        r = record
        if collection == "heads":
            self._execute(
                """
                INSERT INTO playthrough_heads VALUES (?,?,?,?,?)
                ON CONFLICT(playthrough_id) DO UPDATE SET
                    branch_id=excluded.branch_id,
                    checkpoint_id=excluded.checkpoint_id,
                    head_event_id=excluded.head_event_id,
                    updated_at_utc=excluded.updated_at_utc
                """,
                (
                    r["playthrough_id"],
                    r["branch_id"],
                    r["checkpoint_id"],
                    r["head_event_id"],
                    r["updated_at_utc"],
                ),
            )
        elif collection == "save_slots":
            self._execute(
                """
                INSERT INTO save_slots VALUES (?,?,?,?,?)
                ON CONFLICT(playthrough_id,slot_name) DO UPDATE SET
                    checkpoint_id=excluded.checkpoint_id,
                    label=excluded.label,
                    updated_at_utc=excluded.updated_at_utc
                """,
                (
                    r["playthrough_id"],
                    r["slot_name"],
                    r["checkpoint_id"],
                    r["label"],
                    r["updated_at_utc"],
                ),
            )
        elif collection == "runs":
            self._execute(
                """
                UPDATE runs
                SET finished_at_utc=?, status=?, safe_error=?
                WHERE run_id=? AND status='running'
                """,
                (
                    r["finished_at_utc"],
                    r["status"],
                    r["safe_error"],
                    r["run_id"],
                ),
            )
        else:  # pragma: no cover
            raise StorageError(
                f"SQLite mutable persistence is missing collection: {collection}"
            )

    @staticmethod
    def _decode_row(row: sqlite3.Row) -> dict[str, Any]:
        import json

        record = dict(row)
        json_fields = {
            "metadata_json": "metadata",
            "capabilities_json": "capabilities",
            "payload_json": "payload",
            "state_json": "state",
            "config_json": "config",
            "parameters_json": "parameters",
            "usage_json": "usage",
        }
        for source, destination in json_fields.items():
            if source in record:
                try:
                    record[destination] = json.loads(record.pop(source))
                except (TypeError, json.JSONDecodeError) as exc:
                    raise StorageCorruptError(
                        f"invalid canonical JSON in SQLite column {source}"
                    ) from exc
        for boolean in ("reversible", "effective", "final_eligible"):
            if boolean in record:
                record[boolean] = bool(record[boolean])
        return record

    def _query_snapshot(self) -> dict[str, Any]:
        table_map = {
            "playthroughs": "playthroughs",
            "branches": "branches",
            "story_events": "story_events",
            "checkpoints": "checkpoints",
            "heads": "playthrough_heads",
            "save_slots": "save_slots",
            "feature_selections": "feature_selections",
            "runs": "runs",
            "provider_calls": "provider_calls",
            "assets": "assets",
            "decisions": "decisions",
        }
        collections: dict[str, list[dict[str, Any]]] = {
            name: [] for name in ALL_COLLECTIONS
        }
        try:
            for collection, table in table_map.items():
                rows = self._conn.execute(f"SELECT * FROM {table}").fetchall()
                collections[collection] = [self._decode_row(row) for row in rows]
        except sqlite3.DatabaseError as exc:
            raise self._translate_sqlite(exc) from exc
        return {"format_version": 1, "collections": collections}

    def integrity_check(self) -> dict[str, Any]:
        result = super().integrity_check()
        try:
            sqlite_status = [
                str(row[0]) for row in self._conn.execute("PRAGMA integrity_check")
            ]
            foreign_keys = [
                dict(row) for row in self._conn.execute("PRAGMA foreign_key_check")
            ]
            if sqlite_status != ["ok"]:
                result["errors"].append(f"SQLite integrity_check: {sqlite_status}")
            if foreign_keys:
                result["errors"].append(f"SQLite foreign_key_check: {foreign_keys}")
            mirror = MemoryStorage()
            mirror.import_snapshot(self._query_snapshot())
            if mirror.export_snapshot() != self.export_snapshot():
                result["errors"].append("memory mirror differs from SQLite records")
        except sqlite3.DatabaseError as exc:
            result["errors"].append(str(self._translate_sqlite(exc)))
        except StorageError as exc:
            result["errors"].append(str(exc))
        result["ok"] = not result["errors"]
        result["schema_version"] = self._conn.execute("PRAGMA user_version").fetchone()[
            0
        ]
        result["migration_versions"] = [
            row[0]
            for row in self._conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
        return result

    def checkpoint_wal(self) -> None:
        if self.read_only:
            return
        try:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.DatabaseError as exc:
            raise self._translate_sqlite(exc) from exc

    def close(self) -> None:
        if self._closed:
            return
        if self.wal and not self.read_only:
            self.checkpoint_wal()
        self._conn.close()
        super().close()
