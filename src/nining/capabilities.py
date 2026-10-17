"""Static capability discovery that never performs a paid request."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import AppConfig, ModelConfig


@dataclass(frozen=True)
class Capability:
    state: str
    provider: str
    reason: str
    final_eligible: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _has_env(model: ModelConfig, env: Mapping[str, str]) -> tuple[bool, list[str]]:
    required = [key for key in (model.endpoint_env, model.api_key_env) if key]
    if model.model_env and not model.default_model:
        required.append(model.model_env)
    missing = [key for key in required if not env.get(key)]
    return not missing, missing


def _offline_capability(kind: str, provider: str) -> Capability:
    if kind == "text":
        return Capability("available", "deterministic", "offline deterministic compiler", True)
    if kind == "image":
        return Capability("degraded", "placeholder", "no image model; placeholder only", False)
    return Capability("degraded", "prompt_only", "no TTS; voice specification only", False)


def _fallback_capability(
    kind: str,
    primary: ModelConfig,
    config: AppConfig,
    env: Mapping[str, str],
    primary_reason: str,
) -> Capability:
    chain = primary.fallback_providers or ((primary.fallback_provider,) if primary.fallback_provider else ())
    for name in chain:
        if name in {"deterministic", "placeholder", "prompt_only"}:
            fallback = _offline_capability(kind, name)
            return Capability(
                "degraded",
                fallback.provider,
                f"{primary_reason}; using {fallback.provider}",
                fallback.final_eligible,
            )
        candidate = config.providers.get(name)
        if candidate is None:
            continue
        present, missing = _has_env(candidate, env)
        if present:
            return Capability(
                "degraded",
                candidate.provider,
                f"{primary_reason}; secondary provider statically configured",
                kind == "text",
            )
        primary_reason += f"; {name} missing {', '.join(missing)}"
    return Capability("unavailable", primary.provider, primary_reason, False)


def discover_capabilities(
    config: AppConfig, environ: Mapping[str, str] | None = None
) -> dict[str, Capability]:
    env = os.environ if environ is None else environ
    result: dict[str, Capability] = {}
    for kind, model in (("text", config.text), ("image", config.image), ("voice", config.voice)):
        if kind == "voice" and model.provider == "macos_say" and model.enabled:
            executable = Path(str(model.extras.get("executable", "/usr/bin/say")))
            if executable.is_absolute() and os.access(executable, os.X_OK):
                result[kind] = Capability(
                    "available",
                    "macos_say",
                    "local operating-system preset voice; no network",
                    True,
                )
            else:
                result[kind] = _fallback_capability(
                    kind,
                    model,
                    config,
                    env,
                    "local preset voice executable unavailable",
                )
            continue
        if config.runtime.mode in {"offline", "release-view"}:
            result[kind] = _offline_capability(kind, model.provider)
            continue
        if not model.enabled:
            result[kind] = _fallback_capability(
                kind, model, config, env, "primary provider disabled"
            )
            continue
        if model.provider in {"deterministic", "placeholder", "prompt_only"}:
            result[kind] = _offline_capability(kind, model.provider)
            continue
        present, missing = _has_env(model, env)
        if present:
            result[kind] = Capability(
                "available",
                model.provider,
                "static configuration present; live endpoint not tested",
                kind != "voice" or model.provider not in {"prompt_only", "draft_local"},
            )
        else:
            result[kind] = _fallback_capability(
                kind,
                model,
                config,
                env,
                f"primary missing environment names: {', '.join(missing)}",
            )
    return result


def capabilities_dict(
    config: AppConfig, environ: Mapping[str, str] | None = None
) -> dict[str, dict[str, object]]:
    return {key: value.to_dict() for key, value in discover_capabilities(config, environ).items()}

