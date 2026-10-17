"""Command-line entry point for safe local introspection."""

from __future__ import annotations

import argparse
import fcntl
import json
import shutil
import sys
from pathlib import Path

from . import __version__
from .application import resolve_config_path
from .canonical import canonical_json
from .capabilities import capabilities_dict
from .config import AppConfig, ConfigurationError, load_config
from .loader import MaterialError, load_materials
from .playthrough import PlaythroughManager
from .session import ApplicationSession
from .spec_builder import build_all_scene_specs, build_scene_spec
from .storage import StorageBackend, StorageBusyError, StorageError, open_storage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nining")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate-config", help="parse and validate configuration")
    validate.add_argument("--config", type=Path, default=Path("config.offline.toml"))

    capabilities = sub.add_parser("capabilities", help="show static provider capability state")
    capabilities.add_argument("--config", type=Path, default=Path("config.offline.toml"))

    initialize = sub.add_parser("init", help="initialize a fresh local playthrough")
    initialize.add_argument("--config", type=Path, default=Path("config.offline.toml"))
    initialize.add_argument("--materials", type=Path, required=True)
    initialize.add_argument("--title", default="EmoPlay story")
    initialize.add_argument(
        "--reset",
        action="store_true",
        help="delete the configured FileStorage game state before initialization",
    )

    spec = sub.add_parser("spec", help="compile deterministic time-bounded scene specs")
    spec.add_argument("--materials", type=Path, required=True)
    target = spec.add_mutually_exclusive_group(required=True)
    target.add_argument("--trigger")
    target.add_argument("--all", action="store_true")
    spec.add_argument("--output-dir", type=Path)
    spec.add_argument("--pretty", action="store_true")

    generate = sub.add_parser(
        "generate", help="compile and execute one auditable media operation"
    )
    generate.add_argument("--config", type=Path, default=Path("config.offline.toml"))
    generate.add_argument("--materials", type=Path, required=True)
    generate.add_argument("--trigger", required=True)
    generate.add_argument("--playthrough")
    generate.add_argument("--output-root", type=Path)

    background = sub.add_parser(
        "generate-background",
        help="generate one review-gated chapter background image",
    )
    background.add_argument("--config", type=Path, default=Path("config.offline.toml"))
    background.add_argument("--materials", type=Path, required=True)
    background.add_argument("--trigger", required=True)
    background.add_argument("--playthrough")
    background.add_argument("--output-root", type=Path)

    reference = sub.add_parser(
        "generate-reference", help="generate one auditable visual identity reference"
    )
    reference.add_argument("--config", type=Path, default=Path("config.offline.toml"))
    reference.add_argument("--materials", type=Path, required=True)
    reference.add_argument("--playthrough")
    reference.add_argument("--output-root", type=Path)

    serve = sub.add_parser("serve", help="run the local Story/God presentation")
    serve.add_argument("--config", type=Path, default=Path("config.offline.toml"))
    serve.add_argument("--materials", type=Path, required=True)
    serve.add_argument("--playthrough")
    serve.add_argument("--output-root", type=Path)
    serve.add_argument("--port", type=int, default=0)

    play = sub.add_parser("play", help="manage game-style saves, history, and branches")
    play.add_argument("--config", type=Path, default=Path("config.offline.toml"))
    play_sub = play.add_subparsers(dest="play_command", required=True)

    start = play_sub.add_parser("start")
    start.add_argument("--materials", type=Path, required=True)
    start.add_argument("--title", default="Nining story")

    status = play_sub.add_parser("status")
    status.add_argument("--playthrough", required=True)

    history = play_sub.add_parser("history")
    history.add_argument("--playthrough", required=True)
    history.add_argument("--branch")

    save = play_sub.add_parser("save")
    save.add_argument("--playthrough", required=True)
    save.add_argument("--slot", required=True)
    save.add_argument("--label")

    rewind = play_sub.add_parser("rewind")
    rewind.add_argument("--playthrough", required=True)
    source = rewind.add_mutually_exclusive_group(required=True)
    source.add_argument("--slot")
    source.add_argument("--checkpoint")
    rewind.add_argument("--continue", dest="continue_from", action="store_true")
    rewind.add_argument("--branch-name")

    checkout = play_sub.add_parser("checkout")
    checkout.add_argument("--playthrough", required=True)
    checkout.add_argument("--branch", required=True)
    checkout.add_argument("--checkpoint")

    select = play_sub.add_parser("select")
    select.add_argument("--playthrough", required=True)
    select.add_argument("--feature", required=True)
    select.add_argument("--option", required=True)
    select.add_argument("--ineffective", action="store_true")
    return parser


def _write_spec(path: Path, payload: dict[str, object], *, pretty: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
        if pretty
        else canonical_json(payload)
    )
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text + "\n", encoding="utf-8")
    temporary.replace(path)


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True), flush=True)


def _open_config_storage(config: AppConfig) -> StorageBackend:
    sqlite_path: str | Path = config.storage.sqlite_path
    if sqlite_path != ":memory:":
        sqlite_path = resolve_config_path(config, sqlite_path)
    return open_storage(
        config.storage.backend,
        sqlite_path=sqlite_path,
        file_root=resolve_config_path(config, config.storage.file_root),
        fallback_backend=config.storage.fallback_backend,
        busy_timeout_ms=config.storage.busy_timeout_ms,
        wal=config.storage.wal,
    )


def _reset_file_storage(config: AppConfig) -> bool:
    if config.storage.backend != "file":
        raise ConfigurationError("--reset is supported only for storage.backend=file")
    root = resolve_config_path(config, config.storage.file_root)
    if not root.exists():
        return False
    if not root.is_dir() or root.is_symlink():
        raise StorageError("configured file storage root is not a safe directory")
    protected = {
        Path(root.anchor).resolve(),
        Path.home().resolve(),
        config.source_path.parent.resolve(),
    }
    if root in protected or len(root.parts) < 3:
        raise StorageError("refusing to reset a protected storage path")

    marker_path = root / "format.json"
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StorageError("file storage format marker is missing or invalid") from exc
    if marker != {"format": "nining-file-storage", "version": 1}:
        raise StorageError("file storage format marker is unsupported")

    allowed = {".writer.lock", "format.json", "records", "mutable"}
    unexpected = sorted(path.name for path in root.iterdir() if path.name not in allowed)
    if unexpected:
        raise StorageError(
            f"file storage contains unexpected entries: {', '.join(unexpected)}"
        )

    lock_path = root / ".writer.lock"
    with lock_path.open("a+b") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise StorageBusyError(
                "file storage already has an active writer; stop the service first"
            ) from exc
        try:
            for name in ("records", "mutable"):
                target = root / name
                if target.is_symlink():
                    raise StorageError("file storage contains an unsafe symlink")
                if target.exists():
                    if not target.is_dir():
                        raise StorageError("file storage collection is not a directory")
                    shutil.rmtree(target)
            marker_path.unlink()
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return True


def _init_command(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
        reset_performed = _reset_file_storage(config) if args.reset else False
        materials = load_materials(args.materials)
        storage = _open_config_storage(config)
        manager = PlaythroughManager(storage)
        try:
            result = manager.start(
                title=args.title,
                materials_manifest_sha256=materials.manifest["sha256"],
                metadata={"materials_root": materials.root_name},
            )
            playthrough_id = str(result["playthrough"]["playthrough_id"])
            checkpoint_id = str(result["checkpoint"]["checkpoint_id"])
            initial_trigger_id = (
                materials.triggers[0].trigger_id if materials.triggers else None
            )
            _print_json(
                {
                    "status": "ok",
                    "reset_requested": bool(args.reset),
                    "reset_performed": reset_performed,
                    "storage_backend": storage.backend_name,
                    "playthrough_id": playthrough_id,
                    "checkpoint_id": checkpoint_id,
                    "initial_trigger_id": initial_trigger_id,
                    "serve": {
                        "config": str(args.config),
                        "materials": str(args.materials),
                        "playthrough": playthrough_id,
                    },
                }
            )
            return 0
        finally:
            storage.close()
    except (
        ConfigurationError,
        MaterialError,
        StorageError,
        KeyError,
        OSError,
        ValueError,
    ) as exc:
        _print_json({"status": "error", "error": str(exc)})
        return 2


def _play_command(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
        storage = _open_config_storage(config)
        manager = PlaythroughManager(storage)
        try:
            if args.play_command == "start":
                materials = load_materials(args.materials)
                result: object = manager.start(
                    title=args.title,
                    materials_manifest_sha256=materials.manifest["sha256"],
                    metadata={"materials_root": materials.root_name},
                )
            elif args.play_command == "status":
                result = {
                    "head": storage.get_head(args.playthrough),
                    "branches": storage.list_branches(args.playthrough),
                    "save_slots": storage.list_save_slots(args.playthrough),
                    "features": storage.list_feature_selections(args.playthrough),
                }
            elif args.play_command == "history":
                result = manager.history(args.playthrough, branch_id=args.branch)
            elif args.play_command == "save":
                result = manager.save(
                    args.playthrough, slot_name=args.slot, label=args.label
                )
            elif args.play_command == "rewind":
                result = manager.rewind(
                    args.playthrough,
                    slot_name=args.slot,
                    checkpoint_id=args.checkpoint,
                    inspect_only=not args.continue_from,
                    branch_name=args.branch_name,
                ).to_dict()
            elif args.play_command == "checkout":
                result = manager.checkout(
                    args.playthrough,
                    branch_id=args.branch,
                    checkpoint_id=args.checkpoint,
                )
            elif args.play_command == "select":
                result = manager.select_feature(
                    args.playthrough,
                    feature_key=args.feature,
                    option_key=args.option,
                    effective=not args.ineffective,
                )
            else:
                return 2
            _print_json({"status": "ok", "result": result})
            return 0
        finally:
            storage.close()
    except (ConfigurationError, MaterialError, StorageError, KeyError, ValueError) as exc:
        _print_json({"status": "error", "error": str(exc)})
        return 2


def _generate_command(args: argparse.Namespace) -> int:
    try:
        with ApplicationSession.open(
            config_path=args.config,
            materials_root=args.materials,
            playthrough_id=args.playthrough,
            output_root=args.output_root,
        ) as session:
            outcome = session.service.generate(
                args.trigger, playthrough_id=session.playthrough_id
            )
            _print_json({"status": "ok", "result": outcome.to_dict()})
            return 0
    except (
        ConfigurationError,
        MaterialError,
        StorageError,
        KeyError,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        _print_json({"status": "error", "error": str(exc)})
        return 2


def _background_command(args: argparse.Namespace) -> int:
    try:
        with ApplicationSession.open(
            config_path=args.config,
            materials_root=args.materials,
            playthrough_id=args.playthrough,
            output_root=args.output_root,
        ) as session:
            outcome = session.service.generate_background(
                args.trigger, playthrough_id=session.playthrough_id
            )
            _print_json({"status": "ok", "result": outcome.to_dict()})
            return 0
    except (
        ConfigurationError,
        MaterialError,
        StorageError,
        KeyError,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        _print_json({"status": "error", "error": str(exc)})
        return 2


def _reference_command(args: argparse.Namespace) -> int:
    try:
        with ApplicationSession.open(
            config_path=args.config,
            materials_root=args.materials,
            playthrough_id=args.playthrough,
            output_root=args.output_root,
        ) as session:
            outcome = session.service.generate_visual_reference(
                playthrough_id=session.playthrough_id
            )
            _print_json({"status": "ok", "result": outcome.to_dict()})
            return 0
    except (
        ConfigurationError,
        MaterialError,
        StorageError,
        KeyError,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        _print_json({"status": "error", "error": str(exc)})
        return 2


def _serve_command(args: argparse.Namespace) -> int:
    server = None
    session = None
    try:
        if not 0 <= args.port <= 65535:
            raise ValueError("port must be between 0 and 65535")
        session = ApplicationSession.open(
            config_path=args.config,
            materials_root=args.materials,
            playthrough_id=args.playthrough,
            output_root=args.output_root,
        )
        server = session.create_server(port=args.port)
        host = str(server.server_address[0])
        port = int(server.server_address[1])
        status = session.status()
        _print_json(
            {
                "status": "ready",
                "url": f"http://{host}:{port}/",
                **status,
                "resume": (
                    "nining serve --config <config> --materials <materials> "
                    f"--playthrough {session.playthrough_id} --port 0"
                ),
            }
        )
        try:
            server.serve_forever(poll_interval=0.25)
        except KeyboardInterrupt:
            return 0
        return 0
    except (
        ConfigurationError,
        MaterialError,
        StorageError,
        KeyError,
        OSError,
        ValueError,
    ) as exc:
        _print_json({"status": "error", "error": str(exc)})
        return 2
    finally:
        if server is not None:
            server.server_close()
        if session is not None:
            session.close()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "spec":
        try:
            materials = load_materials(args.materials)
            specs = (
                build_all_scene_specs(materials)
                if args.all
                else (build_scene_spec(materials, args.trigger),)
            )
        except (MaterialError, KeyError, ValueError) as exc:
            print(
                json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False),
                file=sys.stderr,
            )
            return 2
        payloads = [item.to_dict() for item in specs]
        if args.output_dir is not None:
            for payload in payloads:
                _write_spec(
                    args.output_dir / f"{payload['trigger_id']}.json",
                    payload,
                    pretty=args.pretty,
                )
        output: object = payloads if args.all else payloads[0]
        print(
            json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2 if args.pretty else None)
        )
        return 0
    if args.command == "play":
        return _play_command(args)
    if args.command == "init":
        return _init_command(args)
    if args.command == "generate":
        return _generate_command(args)
    if args.command == "generate-background":
        return _background_command(args)
    if args.command == "generate-reference":
        return _reference_command(args)
    if args.command == "serve":
        return _serve_command(args)
    try:
        config = load_config(args.config)
    except ConfigurationError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    if args.command == "validate-config":
        print(
            json.dumps(
                {
                    "status": "ok",
                    "mode": config.runtime.mode,
                    "storage": config.storage.backend,
                    "config_sha256": config.fingerprint(),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "capabilities":
        print(
            json.dumps(
                {
                    "status": "ok",
                    "mode": config.runtime.mode,
                    "capabilities": capabilities_dict(config),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

