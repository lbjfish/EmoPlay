#!/usr/bin/env python3
"""Export sanitized provider-call records from portable state into JSONL."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from nining.application import resolve_config_path
from nining.config import load_config
from nining.storage import open_storage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    sqlite_path: str | Path = config.storage.sqlite_path
    if sqlite_path != ":memory:":
        sqlite_path = resolve_config_path(config, sqlite_path)
    storage = open_storage(
        config.storage.backend,
        sqlite_path=sqlite_path,
        file_root=resolve_config_path(config, config.storage.file_root),
        fallback_backend=config.storage.fallback_backend,
        busy_timeout_ms=config.storage.busy_timeout_ms,
        wal=config.storage.wal,
        read_only=True,
    )
    try:
        records = storage.list_provider_calls()
    finally:
        storage.close()
    output = args.output or (
        resolve_config_path(config, config.logging.directory)
        / "provider_calls.jsonl"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            )
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(output)
    print(json.dumps({"records": len(records), "output": output.name}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
