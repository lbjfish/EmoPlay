from __future__ import annotations

import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any

from nining.cli import main
from nining.storage import FileStorage

ROOT = Path(__file__).resolve().parents[1]


def run_cli(arguments: list[str]) -> dict[str, Any]:
    output = StringIO()
    with redirect_stdout(output):
        assert main(arguments) == 0
    return json.loads(output.getvalue())


def run_cli_result(arguments: list[str]) -> tuple[int, dict[str, Any]]:
    output = StringIO()
    with redirect_stdout(output):
        code = main(arguments)
    return code, json.loads(output.getvalue())


def write_config(path: Path, state_root: Path) -> None:
    path.write_text(
        f"""
[runtime]
mode = "offline"
allow_network = false
[storage]
backend = "file"
file_root = "{state_root.as_posix()}"
[playthrough]
autosave = true
checkpoint_on = ["trigger"]
rewind_continue_policy = "create_branch"
preserve_provider_calls_on_rewind = true
[models.text]
provider = "deterministic"
[models.image]
provider = "placeholder"
[models.voice]
provider = "prompt_only"
""",
        encoding="utf-8",
    )


def test_init_reset_replaces_local_state_with_fresh_t1_playthrough(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.toml"
    state_root = tmp_path / "state"
    write_config(config, state_root)
    common = [
        "init",
        "--config",
        str(config),
        "--materials",
        str(ROOT / "materials" / "sample"),
    ]

    first = run_cli(common + ["--title", "old state"])
    old_playthrough = first["playthrough_id"]
    run_cli(
        [
            "play",
            "--config",
            str(config),
            "save",
            "--playthrough",
            old_playthrough,
            "--slot",
            "old-slot",
        ]
    )

    reset = run_cli(common + ["--reset", "--title", "fresh state"])

    assert reset["reset_requested"] is True
    assert reset["reset_performed"] is True
    assert reset["initial_trigger_id"] == "T1"
    assert reset["playthrough_id"] != old_playthrough
    assert reset["serve"]["playthrough"] == reset["playthrough_id"]
    playthrough_files = list((state_root / "records" / "playthroughs").glob("*.json"))
    assert len(playthrough_files) == 1
    assert old_playthrough not in playthrough_files[0].name
    assert list((state_root / "mutable" / "save_slots").glob("*.json")) == []


def test_init_reset_refuses_active_or_unrecognized_file_storage(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    state_root = tmp_path / "state"
    write_config(config, state_root)
    common = [
        "init",
        "--config",
        str(config),
        "--materials",
        str(ROOT / "materials" / "sample"),
        "--reset",
    ]

    writer = FileStorage(state_root)
    try:
        code, busy = run_cli_result(common)
    finally:
        writer.close()
    assert code == 2
    assert "active writer" in busy["error"]

    (state_root / "unexpected.txt").write_text("preserve", encoding="utf-8")
    code, unsafe = run_cli_result(common)
    assert code == 2
    assert "unexpected entries" in unsafe["error"]
    assert (state_root / "unexpected.txt").read_text(encoding="utf-8") == "preserve"


def test_play_start_save_rewind_branch_and_feature(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    write_config(config, tmp_path / "state")
    common = ["play", "--config", str(config)]

    started = run_cli(
        common
        + [
            "start",
            "--materials",
            str(ROOT / "materials" / "sample"),
            "--title",
            "test story",
        ]
    )
    playthrough = started["result"]["playthrough"]["playthrough_id"]
    run_cli(common + ["save", "--playthrough", playthrough, "--slot", "start"])
    selected = run_cli(
        common
        + [
            "select",
            "--playthrough",
            playthrough,
            "--feature",
            "view.mode",
            "--option",
            "god",
        ]
    )
    assert selected["result"]["selection"]["option_key"] == "god"
    branched = run_cli(
        common
        + [
            "rewind",
            "--playthrough",
            playthrough,
            "--slot",
            "start",
            "--continue",
            "--branch-name",
            "alternate",
        ]
    )
    assert branched["result"]["inspect_only"] is False
    status = run_cli(common + ["status", "--playthrough", playthrough])
    assert len(status["result"]["branches"]) == 2
    assert status["result"]["head"]["branch_id"] == branched["result"]["branch_id"]
