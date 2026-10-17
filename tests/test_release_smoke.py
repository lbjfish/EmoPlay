from __future__ import annotations

import errno
import json
import socket
from pathlib import Path

import pytest

from nining.release_smoke import _loopback_only_network, run_offline_app_smoke

ROOT = Path(__file__).resolve().parents[1]


def test_offline_app_smoke_exercises_loopback_app_and_persistence() -> None:
    result = run_offline_app_smoke(ROOT)

    assert result.ok is True
    assert result.status == "passed"
    assert [step.name for step in result.steps] == [
        "prepare",
        "validate_config",
        "capabilities",
        "spec_all",
        "generate_t1",
        "browser_shell",
        "local_web",
        "position_action",
        "reopen_storage",
    ]
    payload = json.dumps(result.to_public_dict(), ensure_ascii=False)
    assert str(ROOT) not in payload
    assert "/" + "Users/" not in payload
    assert "api_" + "key" not in payload.lower()
    assert "token" not in payload.lower()


def test_loopback_only_network_blocks_non_loopback_socket() -> None:
    with (
        _loopback_only_network(),
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocked,
        pytest.raises(PermissionError, match="non-loopback"),
    ):
        blocked.connect(("198.51.100.1", 443))
    with (
        _loopback_only_network(),
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocked_ex,
    ):
        assert blocked_ex.connect_ex(("198.51.100.1", 443)) == errno.EPERM


def test_smoke_failure_output_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_copy(_repo_root: Path, _temp_root: Path) -> Path:
        raise RuntimeError(
            "/" + "Users/example/private api_" + "key=abcdefghijklmnopqrstuvwx"
        )

    monkeypatch.setattr("nining.release_smoke._copy_sample_materials", fail_copy)

    result = run_offline_app_smoke(ROOT)

    assert result.ok is False
    payload = json.dumps(result.to_public_dict(), ensure_ascii=False)
    assert "/" + "Users/" not in payload
    assert "abcdefghijklmnopqrstuvwx" not in payload
    assert "<redacted>" in payload
