"""Lifecycle owner for config, materials, storage, services, and local web."""

from __future__ import annotations

from pathlib import Path
from typing import Self

from .application import GenerationService, resolve_config_path
from .config import AppConfig, load_config
from .loader import load_materials
from .position import StoryPositionService
from .release_catalog import load_release_media_catalog
from .review import ReviewService
from .storage import open_storage
from .storage.protocol import StorageBackend
from .web import RepositoryWebFacade, create_server
from .web.server import LocalWebHTTPServer


class ApplicationSession:
    def __init__(
        self,
        *,
        config: AppConfig,
        materials_root: Path,
        storage: StorageBackend,
        service: GenerationService,
        playthrough_id: str,
    ) -> None:
        self.config = config
        self.materials_root = materials_root
        self.storage = storage
        self.service = service
        self.playthrough_id = playthrough_id
        self.position = StoryPositionService(
            materials=service.materials,
            storage=storage,
            manager=service.manager,
        )
        self.review = ReviewService(
            storage=storage,
            manager=service.manager,
            asset_root=service.output_root,
        )
        release_assets = load_release_media_catalog(service.output_root)

        def presentation_assets(trigger_id: str | None) -> list[dict[str, object]]:
            records = [dict(item) for item in storage.list_assets(playthrough_id)]
            seen = {
                (
                    item.get("trigger_id"),
                    item.get("media_type"),
                    item.get("sha256"),
                )
                for item in records
            }
            for item in release_assets:
                identity = (
                    item.get("trigger_id"),
                    item.get("media_type"),
                    item.get("sha256"),
                )
                if identity not in seen:
                    records.append(dict(item))
                    seen.add(identity)
            if trigger_id is None:
                return records
            return [item for item in records if item.get("trigger_id") == trigger_id]

        self._closed = False
        self.facade = RepositoryWebFacade(
            materials=service.materials,
            config=config,
            manager=service.manager,
            storage=storage,
            playthrough_id=playthrough_id,
            asset_root=service.output_root,
            asset_provider=presentation_assets,
            generation_provider=lambda trigger_id, expected_checkpoint_id: service.generate(
                trigger_id,
                playthrough_id=playthrough_id,
                expected_checkpoint_id=expected_checkpoint_id,
            ).to_dict(),
            position_provider=lambda: self.position.current(playthrough_id),
            position_action=lambda trigger_id, view_mode, expected_checkpoint_id: self.position.move(
                playthrough_id,
                trigger_id=trigger_id,
                view_mode=view_mode,
                expected_checkpoint_id=expected_checkpoint_id,
            ),
            review_provider=lambda asset_id, action, reason, expected_sha256: self.review.review(
                playthrough_id,
                asset_id=asset_id,
                action=action,
                reason=reason,
                expected_sha256=expected_sha256,
            ),
        )

    @classmethod
    def open(
        cls,
        *,
        config_path: str | Path,
        materials_root: str | Path,
        playthrough_id: str | None = None,
        output_root: str | Path | None = None,
    ) -> Self:
        config = load_config(config_path)
        materials_path = Path(materials_root).expanduser().resolve()
        materials = load_materials(materials_path)
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
        )
        try:
            service = GenerationService(
                config=config,
                materials=materials,
                storage=storage,
                output_root=output_root,
            )
            resolved_playthrough = service.ensure_playthrough(playthrough_id)
            return cls(
                config=config,
                materials_root=materials_path,
                storage=storage,
                service=service,
                playthrough_id=resolved_playthrough,
            )
        except Exception:
            storage.close()
            raise

    def status(self) -> dict[str, object]:
        position = self.position.current(self.playthrough_id)
        return {
            "playthrough_id": self.playthrough_id,
            "branch_id": position["branch_id"],
            "checkpoint_id": position["checkpoint_id"],
            "trigger_id": position["trigger_id"],
            "view_mode": position["view_mode"],
            "mode": self.config.runtime.mode,
            "network_allowed": self.config.runtime.allow_network,
            "storage_backend": self.storage.backend_name,
            "storage_fallback_reason": self.storage.fallback_reason,
            "capabilities": self.service.capabilities,
        }

    def create_server(self, *, port: int = 0) -> LocalWebHTTPServer:
        if self._closed:
            raise RuntimeError("application session is closed")
        return create_server(self.facade, port=port)

    def close(self) -> None:
        if self._closed:
            return
        self.storage.close()
        self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
