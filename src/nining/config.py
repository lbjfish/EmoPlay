"""Configuration loading with explicit environment indirection and redaction."""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any


class ConfigurationError(ValueError):
    """Raised when project configuration is invalid."""


RUNTIME_MODES = {"offline", "mock", "local", "hybrid", "azure", "release-view"}
STORAGE_BACKENDS = {"auto", "sqlite", "file", "memory"}
SENSITIVE_KEY_PARTS = ("key", "token", "secret", "password", "authorization", "cookie")


@dataclass(frozen=True)
class RuntimeConfig:
    mode: str = "offline"
    allow_network: bool = False
    strict_provenance: bool = True
    output_root: str = "outputs"


@dataclass(frozen=True)
class StorageConfig:
    backend: str = "file"
    sqlite_path: str = "work/state/nining.sqlite3"
    file_root: str = "work/state/files"
    fallback_backend: str = "file"
    database_is_rebuildable_index: bool = True
    busy_timeout_ms: int = 5000
    wal: bool = False


@dataclass(frozen=True)
class PlaythroughConfig:
    autosave: bool = True
    checkpoint_on: tuple[str, ...] = (
        "trigger",
        "pre_generation",
        "post_generation",
        "human_decision",
    )
    rewind_continue_policy: str = "create_branch"
    preserve_provider_calls_on_rewind: bool = True


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    endpoint_env: str | None = None
    api_key_env: str | None = None
    model_env: str | None = None
    default_model: str | None = None
    fallback_provider: str | None = None
    fallback_providers: tuple[str, ...] = ()
    enabled: bool = True
    extras: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LoggingConfig:
    directory: str = "logs"
    redact_headers: tuple[str, ...] = ("authorization", "api-key", "cookie")
    save_raw_provider_responses: bool = False


@dataclass(frozen=True)
class TestingConfig:
    live_tests_env: str = "RUN_LIVE_TESTS"
    fixture_root: str = "tests/fixtures"
    block_network_by_default: bool = True


@dataclass(frozen=True)
class AppConfig:
    runtime: RuntimeConfig
    storage: StorageConfig
    playthrough: PlaythroughConfig
    text: ModelConfig
    image: ModelConfig
    voice: ModelConfig
    providers: Mapping[str, ModelConfig]
    logging: LoggingConfig
    testing: TestingConfig
    source_path: Path

    def safe_snapshot(self, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
        """Return serializable config metadata without secret values."""

        env = os.environ if environ is None else environ
        result: dict[str, Any] = {
            "runtime": asdict(self.runtime),
            "storage": asdict(self.storage),
            "playthrough": asdict(self.playthrough),
            "models": {},
            "logging": asdict(self.logging),
            "testing": asdict(self.testing),
            "source_name": self.source_path.name,
        }
        for name, model in (("text", self.text), ("image", self.image), ("voice", self.voice)):
            model_data = asdict(model)
            model_data["extras"] = _redact_mapping(model_data.get("extras", {}))
            model_data["environment"] = {
                key: bool(key and env.get(key))
                for key in (model.endpoint_env, model.api_key_env, model.model_env)
                if key
            }
            result["models"][name] = model_data
        result["providers"] = {
            name: {
                **asdict(provider),
                "extras": _redact_mapping(provider.extras),
                "environment": {
                    key: bool(key and env.get(key))
                    for key in (provider.endpoint_env, provider.api_key_env, provider.model_env)
                    if key
                },
            }
            for name, provider in self.providers.items()
        }
        return result

    def fingerprint(self, environ: Mapping[str, str] | None = None) -> str:
        payload = json.dumps(
            self.safe_snapshot(environ),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(payload).hexdigest()


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigurationError(f"{path} must be a TOML table")
    return dict(value)


def _redact_mapping(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]"
                if any(part in str(key).lower() for part in SENSITIVE_KEY_PARTS)
                else _redact_mapping(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_redact_mapping(item) for item in value]
    return value


def _string_tuple(value: Any, path: str) -> tuple[str, ...]:
    if not isinstance(value, list | tuple) or not all(isinstance(item, str) for item in value):
        raise ConfigurationError(f"{path} must be an array of strings")
    return tuple(value)


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigurationError(f"{path} must be a boolean")
    return value


def _integer(value: Any, path: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigurationError(
            f"{path} must be an integer greater than or equal to {minimum}"
        )
    return value


def _model(data: Mapping[str, Any], name: str) -> ModelConfig:
    values = dict(data)
    provider = values.pop("provider", None)
    if not isinstance(provider, str) or not provider.strip():
        raise ConfigurationError(f"models.{name}.provider must be a non-empty string")
    fallback_providers = _string_tuple(
        values.pop("fallback_providers", ()),
        f"{name}.fallback_providers",
    )
    known = {
        "endpoint_env": values.pop("endpoint_env", None),
        "api_key_env": values.pop("api_key_env", None),
        "model_env": values.pop("model_env", None),
        "default_model": values.pop("default_model", None),
        "fallback_provider": values.pop("fallback_provider", None),
        "fallback_providers": fallback_providers,
        "enabled": _boolean(values.pop("enabled", True), f"models.{name}.enabled"),
    }
    for key in ("endpoint_env", "api_key_env", "model_env", "default_model", "fallback_provider"):
        if known[key] is not None and not isinstance(known[key], str):
            raise ConfigurationError(f"models.{name}.{key} must be a string")
    return ModelConfig(provider=provider, extras=values, **known)


def load_config(path: str | Path) -> AppConfig:
    source = Path(path).expanduser().resolve()
    try:
        with source.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigurationError(f"config not found: {source}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"invalid TOML in {source.name}: {exc}") from exc

    runtime_data = _mapping(data.get("runtime"), "runtime")
    storage_data = _mapping(data.get("storage"), "storage")
    play_data = _mapping(data.get("playthrough"), "playthrough")
    models = _mapping(data.get("models"), "models")
    providers_data = _mapping(data.get("providers"), "providers")
    logging_data = _mapping(data.get("logging"), "logging")
    testing_data = _mapping(data.get("testing"), "testing")

    mode = runtime_data.get("mode", "offline")
    if mode not in RUNTIME_MODES:
        raise ConfigurationError(f"runtime.mode must be one of {sorted(RUNTIME_MODES)}")
    backend = storage_data.get("backend", "file")
    fallback = storage_data.get("fallback_backend", "file")
    if backend not in STORAGE_BACKENDS:
        raise ConfigurationError(f"storage.backend must be one of {sorted(STORAGE_BACKENDS)}")
    if fallback not in STORAGE_BACKENDS - {"auto"}:
        raise ConfigurationError("storage.fallback_backend must be sqlite, file, or memory")

    checkpoint_on = _string_tuple(
        play_data.get("checkpoint_on", PlaythroughConfig.checkpoint_on),
        "playthrough.checkpoint_on",
    )
    redact_headers = _string_tuple(
        logging_data.get("redact_headers", ("authorization", "api-key", "cookie")),
        "logging.redact_headers",
    )

    return AppConfig(
        runtime=RuntimeConfig(
            mode=mode,
            allow_network=_boolean(
                runtime_data.get("allow_network", False), "runtime.allow_network"
            ),
            strict_provenance=_boolean(
                runtime_data.get("strict_provenance", True),
                "runtime.strict_provenance",
            ),
            output_root=str(runtime_data.get("output_root", "outputs")),
        ),
        storage=StorageConfig(
            backend=backend,
            sqlite_path=str(storage_data.get("sqlite_path", "work/state/nining.sqlite3")),
            file_root=str(storage_data.get("file_root", "work/state/files")),
            fallback_backend=fallback,
            database_is_rebuildable_index=_boolean(
                storage_data.get("database_is_rebuildable_index", True),
                "storage.database_is_rebuildable_index",
            ),
            busy_timeout_ms=_integer(
                storage_data.get("busy_timeout_ms", 5000),
                "storage.busy_timeout_ms",
            ),
            wal=_boolean(storage_data.get("wal", False), "storage.wal"),
        ),
        playthrough=PlaythroughConfig(
            autosave=_boolean(play_data.get("autosave", True), "playthrough.autosave"),
            checkpoint_on=checkpoint_on,
            rewind_continue_policy=str(
                play_data.get("rewind_continue_policy", "create_branch")
            ),
            preserve_provider_calls_on_rewind=_boolean(
                play_data.get("preserve_provider_calls_on_rewind", True),
                "playthrough.preserve_provider_calls_on_rewind",
            ),
        ),
        text=_model(_mapping(models.get("text"), "models.text"), "text"),
        image=_model(_mapping(models.get("image"), "models.image"), "image"),
        voice=_model(_mapping(models.get("voice"), "models.voice"), "voice"),
        providers={
            name: _model(_mapping(value, f"providers.{name}"), f"providers.{name}")
            for name, value in providers_data.items()
        },
        logging=LoggingConfig(
            directory=str(logging_data.get("directory", "logs")),
            redact_headers=redact_headers,
            save_raw_provider_responses=_boolean(
                logging_data.get("save_raw_provider_responses", False),
                "logging.save_raw_provider_responses",
            ),
        ),
        testing=TestingConfig(
            live_tests_env=str(testing_data.get("live_tests_env", "RUN_LIVE_TESTS")),
            fixture_root=str(testing_data.get("fixture_root", "tests/fixtures")),
            block_network_by_default=_boolean(
                testing_data.get("block_network_by_default", True),
                "testing.block_network_by_default",
            ),
        ),
        source_path=source,
    )

