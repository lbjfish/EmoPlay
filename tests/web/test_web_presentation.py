from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from threading import Barrier
from typing import Any, cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import nining.web.server as web_server
from nining.config import load_config
from nining.loader import load_materials
from nining.playthrough import PlaythroughManager
from nining.review import ReviewService
from nining.storage import MemoryStorage, StorageValidationError
from nining.storage.protocol import StorageBackend
from nining.web import (
    LocalWebServer,
    RepositoryWebFacade,
    WebFacadeError,
    create_server,
)

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "src" / "nining" / "web" / "assets"
FRONTEND_SOURCE = ROOT / "frontend" / "src"
HASH_A = "a" * 64
HASH_B = "b" * 64


def _request(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    headers.update(extra_headers or {})
    request = Request(url, method=method, data=data, headers=headers)
    try:
        with urlopen(request, timeout=3) as response:
            return response.status, dict(response.headers.items()), response.read()
    except HTTPError as error:
        return error.code, dict(error.headers.items()), error.read()


def _started_facade(tmp_path: Path) -> tuple[RepositoryWebFacade, MemoryStorage]:
    materials = load_materials(ROOT / "materials" / "sample")
    config = load_config(ROOT / "config.offline.toml")
    storage = MemoryStorage()
    backend = cast(StorageBackend, storage)
    manager = PlaythroughManager(backend)
    started = manager.start(
        title="local web test",
        materials_manifest_sha256=materials.manifest["sha256"],
    )
    playthrough_id = started["playthrough"]["playthrough_id"]
    run = storage.begin_run(
        playthrough_id=playthrough_id,
        branch_id=started["branch"]["branch_id"],
        mode="mock",
        requested_storage="memory",
        effective_storage="memory",
        materials_manifest_sha256=materials.manifest["sha256"],
        config_sha256=HASH_A,
    )
    media_root = tmp_path / "media"
    media_root.mkdir()
    assets = (
        ("placeholder", "placeholder.png", "fixture", False),
        ("draft", "draft.png", "local", False),
        ("final", "final.png", "manual", True),
    )
    for role, filename, provenance, final_eligible in assets:
        content = f"safe-local-{role}-test-media".encode()
        path = media_root / filename
        path.write_bytes(content)
        storage.register_asset(
            run_id=run["run_id"],
            role=role,
            media_type="image",
            relative_path=filename,
            size_bytes=len(content),
            sha256=sha256(content).hexdigest(),
            provenance=provenance,
            final_eligible=final_eligible,
            trigger_id=materials.triggers[0].trigger_id,
            mime_type="image/png",
        )
    review = ReviewService(storage=backend, manager=manager, asset_root=media_root)
    return (
        RepositoryWebFacade(
            materials=materials,
            config=config,
            manager=manager,
            storage=backend,
            playthrough_id=playthrough_id,
            asset_root=media_root,
            review_provider=lambda asset_id, action, reason, expected_sha256: review.review(
                playthrough_id,
                asset_id=asset_id,
                action=action,
                reason=reason,
                expected_sha256=expected_sha256,
            ),
        ),
        storage,
    )


def test_facade_uses_materials_for_timeline_context_and_capabilities(
    tmp_path: Path,
) -> None:
    facade, storage = _started_facade(tmp_path)
    try:
        materials = load_materials(ROOT / "materials" / "sample")
        story = facade.dashboard(view_mode="story")
        timeline = story["timeline"]
        assert [item["trigger_id"] for item in timeline] == [
            item.trigger_id for item in materials.triggers
        ]
        assert len(timeline) == 6
        assert timeline[0]["locked"] is False
        assert all(item["locked"] for item in timeline[1:])
        assert "all" not in story["scene"]["message_flow"]
        assert story["scene"]["future_locks"]
        assert (
            story["scene"]["scene_spec"]["trigger_id"]
            == materials.triggers[0].trigger_id
        )
        assert story["scene"]["eligibility"]["scene_spec_final_eligible"] is False
        assert story["capabilities"]["image"]["state"] == "degraded"
        assert story["capabilities"]["image"]["final_eligible"] is False

        selected = materials.triggers[2].trigger_id
        god = facade.dashboard(view_mode="god", trigger_id=selected)
        assert not any(item["locked"] for item in god["timeline"])
        assert len(god["scene"]["message_flow"]["all"]) == len(materials.messages)
        assert god["scene"]["scene_spec"]["future_locked_message_ids"]
    finally:
        storage.close()


def test_facade_adapts_registered_media_and_playthrough_controls(
    tmp_path: Path,
) -> None:
    facade, storage = _started_facade(tmp_path)
    try:
        dashboard = facade.dashboard(view_mode="story")
        assets_by_role = {asset["role"]: asset for asset in dashboard["assets"]}
        assert assets_by_role["placeholder"]["final_eligible"] is False
        assert assets_by_role["draft"]["final_eligible"] is False
        assert assets_by_role["final"]["final_eligible"] is True
        asset = assets_by_role["placeholder"]
        assert asset["preview_available"] is True
        assert facade.media(asset["asset_id"]) is not None
        controls = dashboard["controls"]
        assert controls["state"] == "available"
        checkpoint_id = controls["head"]["checkpoint_id"]
        assert isinstance(checkpoint_id, str)
        assert set(controls["head"]) == {"checkpoint_id"}
        assert not {
            "checkpoints",
            "save_slots",
            "branches",
            "feature_selections",
        }.intersection(controls)
        with pytest.raises(WebFacadeError, match="unknown control action"):
            facade.action(
                "save",
                {"slot_name": "removed", "expected_checkpoint_id": checkpoint_id},
            )
    finally:
        storage.close()


def test_server_only_binds_loopback_and_exposes_safe_api(tmp_path: Path) -> None:
    facade, storage = _started_facade(tmp_path)
    try:
        with pytest.raises(ValueError, match="127.0.0.1"):
            create_server(facade, host="0.0.0.0")
        with LocalWebServer(facade) as local:
            assert local.host == "127.0.0.1"
            status, headers, index_html = _request(local.url)
            assert status == 200
            assert b"EmoPlay" in index_html
            assert local.action_token.encode("ascii") in index_html
            assert b"__NINING_ACTION_TOKEN__" not in index_html
            assert "default-src 'self'" in headers["Content-Security-Policy"]

            status, _, data = _request(f"{local.url}api/v1/dashboard?view=story")
            dashboard = json.loads(data)
            assert status == 200
            assert dashboard["view_mode"] == "story"
            selected = dashboard["selected_trigger_id"]
            assert isinstance(selected, str)

            status, _, data = _request(f"{local.url}api/v1/scenes/{selected}?view=god")
            scene = json.loads(data)
            assert status == 200
            assert scene["view_mode"] == "god"
            assert "all" in scene["message_flow"]

            asset_id = dashboard["assets"][0]["asset_id"]
            status, _, media = _request(f"{local.url}media/{asset_id}")
            assert status == 200
            assert media == b"safe-local-placeholder-test-media"

            status, _, data = _request(
                f"{local.url}api/v1/actions/review_reject",
                method="POST",
                payload={
                    "asset_id": asset_id,
                    "reason": "missing token",
                    "operation_id": "op-review-1",
                },
            )
            assert status == 403

            action_headers = {
                "X-Nining-Action-Token": local.action_token,
                "Origin": local.url.rstrip("/"),
            }
            checkpoint_id = dashboard["controls"]["head"]["checkpoint_id"]
            assert isinstance(checkpoint_id, str)
            status, _, data = _request(
                f"{local.url}api/v1/actions/review_reject",
                method="POST",
                payload={
                    "asset_id": asset_id,
                    "reason": "API review test",
                    "expected_checkpoint_id": checkpoint_id,
                    "operation_id": "op-review-1",
                },
                extra_headers=action_headers,
            )
            assert status == 200
            assert json.loads(data)["action"] == "review_reject"

            status, _, data = _request(
                f"{local.url}api/v1/actions/review_reject",
                method="POST",
                payload={
                    "asset_id": asset_id,
                    "reason": "API review test",
                    "expected_checkpoint_id": checkpoint_id,
                    "operation_id": "op-review-1",
                },
                extra_headers=action_headers,
            )
            assert status == 409
            assert "already completed" in json.loads(data)["error"]["message"]

            for index, removed in enumerate(
                ("save", "checkpoint", "rewind", "branch", "checkout", "feature")
            ):
                status, _, data = _request(
                    f"{local.url}api/v1/actions/{removed}",
                    method="POST",
                    payload={"operation_id": f"op-removed-{index}"},
                    extra_headers=action_headers,
                )
                assert status == 400
                assert json.loads(data)["error"]["message"] == "unknown control action"

            status, _, data = _request(f"{local.url}assets/%2e%2e%2fpyproject.toml")
            assert status == 404
            assert json.loads(data)["error"]["code"] == 404
            status, _, data = _request(f"{local.url}media/%2e%2e")
            assert status in {400, 404}
            assert "error" in json.loads(data)
    finally:
        storage.close()


def test_server_serves_fixed_frontend_bundle_with_explicit_security_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixed_assets = {
        "index.html": (
            '<meta name="nining-action-token" content="__NINING_ACTION_TOKEN__">'
        ),
        "game.css": "#root { display: block; }\n",
        "react-core.js": "export const reactCore = true;\n",
        "game.js": "export const game = true;\n",
        "effects.js": "export const effects = true;\n",
        "media-review.js": "export const mediaReview = true;\n",
        "god-charts.js": "export const godCharts = true;\n",
    }
    for name, content in fixed_assets.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    monkeypatch.setattr(web_server, "_STATIC_DIR", tmp_path)

    with LocalWebServer(RepositoryWebFacade()) as local:
        expected_routes = {
            "": "text/html",
            "index.html": "text/html",
            "assets/game.css": "text/css",
            "assets/react-core.js": "application/javascript",
            "assets/game.js": "application/javascript",
            "assets/effects.js": "application/javascript",
            "assets/media-review.js": "application/javascript",
            "assets/god-charts.js": "application/javascript",
        }
        for route, content_type in expected_routes.items():
            status, headers, body = _request(f"{local.url}{route}")
            assert status == 200
            assert headers["Content-Type"].startswith(content_type)
            assert headers["Cache-Control"] == "no-store"
            csp = headers["Content-Security-Policy"]
            assert "default-src 'self'" in csp
            assert "script-src 'self'" in csp
            assert "style-src 'self'" in csp
            assert "style-src-elem 'self'" in csp
            assert "sha256-kAApudxpTi9mfjlC9lC8ZaS9xFHU9/NLLbB173MU7SU=" in csp
            assert "sha256-nzTgYzXYDNe6BAHiiI7NNlfK8n/auuOAhh2t92YvuXo=" in csp
            assert "style-src-attr 'unsafe-inline'" in csp
            assert "connect-src 'self'" in csp
            assert "data:" not in csp
            assert "script-src 'unsafe-inline'" not in csp
            assert "script-src 'unsafe-eval'" not in csp
            assert "http:" not in csp
            assert "https:" not in csp
            if route in {"", "index.html"}:
                assert local.action_token.encode("ascii") in body
                assert b"__NINING_ACTION_TOKEN__" not in body

        status, _, data = _request(f"{local.url}assets/chunk-extra.js")
        assert status == 404
        assert json.loads(data)["error"]["code"] == 404


def test_server_requires_expected_checkpoint_for_every_mutation(tmp_path: Path) -> None:
    facade, storage = _started_facade(tmp_path)
    try:
        with LocalWebServer(facade) as local:
            status, _, data = _request(f"{local.url}api/v1/dashboard?view=story")
            assert status == 200
            dashboard = json.loads(data)
            controls = dashboard["controls"]
            checkpoint_id = controls["head"]["checkpoint_id"]
            assert isinstance(checkpoint_id, str)
            asset_id = dashboard["assets"][0]["asset_id"]
            assert isinstance(asset_id, str)

            action_headers = {
                "X-Nining-Action-Token": local.action_token,
                "Origin": local.url.rstrip("/"),
            }
            actions = (
                ("generate", {"trigger_id": "T1"}),
                ("position", {"trigger_id": "T1", "view_mode": "story"}),
                (
                    "review_accept",
                    {"asset_id": asset_id, "reason": "missing precondition"},
                ),
                (
                    "review_reject",
                    {"asset_id": asset_id, "reason": "missing precondition"},
                ),
            )
            for index, (action, payload) in enumerate(actions):
                status, _, data = _request(
                    f"{local.url}api/v1/actions/{action}",
                    method="POST",
                    payload={**payload, "operation_id": f"op-missing-head-{index}"},
                    extra_headers=action_headers,
                )
                assert status == 400
                assert "expected_checkpoint_id" in json.loads(data)["error"]["message"]

            assert storage.get_head(controls["playthrough_id"])["checkpoint_id"] == checkpoint_id
    finally:
        storage.close()


def test_server_serializes_stale_mutations_before_writing(tmp_path: Path) -> None:
    facade, storage = _started_facade(tmp_path)
    try:
        with LocalWebServer(facade) as local:
            status, _, data = _request(f"{local.url}api/v1/dashboard?view=story")
            assert status == 200
            dashboard = json.loads(data)
            controls = dashboard["controls"]
            playthrough_id = controls["playthrough_id"]
            initial_checkpoint_id = controls["head"]["checkpoint_id"]
            assert isinstance(playthrough_id, str)
            assert isinstance(initial_checkpoint_id, str)
            placeholder = next(
                asset for asset in dashboard["assets"] if asset["role"] == "placeholder"
            )
            action_headers = {
                "X-Nining-Action-Token": local.action_token,
                "Origin": local.url.rstrip("/"),
            }
            start = Barrier(2)

            def reject_candidate(operation_id: str) -> tuple[int, dict[str, str], bytes]:
                start.wait(timeout=3)
                return _request(
                    f"{local.url}api/v1/actions/review_reject",
                    method="POST",
                    payload={
                        "asset_id": placeholder["asset_id"],
                        "reason": "parallel review",
                        "expected_checkpoint_id": initial_checkpoint_id,
                        "operation_id": operation_id,
                    },
                    extra_headers=action_headers,
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                responses = list(
                    executor.map(
                        reject_candidate,
                        ("op-parallel-review-1", "op-parallel-review-2"),
                    )
                )
            assert sorted(status for status, _, _ in responses) == [200, 409]
            conflict = next(data for status, _, data in responses if status == 409)
            assert "refresh" in json.loads(conflict)["error"]["message"].lower()
            assert len(storage.list_checkpoints(playthrough_id)) == 2

            for action, payload in (
                ("generate", {"trigger_id": "T1"}),
                ("position", {"trigger_id": "T1", "view_mode": "story"}),
                (
                    "review_reject",
                    {
                        "asset_id": placeholder["asset_id"],
                        "reason": "stale browser state",
                    },
                ),
            ):
                status, _, data = _request(
                    f"{local.url}api/v1/actions/{action}",
                    method="POST",
                    payload={
                        **payload,
                        "expected_checkpoint_id": initial_checkpoint_id,
                        "operation_id": f"op-{action}-stale",
                    },
                    extra_headers=action_headers,
                )
                assert status == 409
                assert "refresh" in json.loads(data)["error"]["message"].lower()

            assert len(storage.list_decisions(playthrough_id)) == 1
    finally:
        storage.close()


def test_server_reports_safe_review_gates_without_storage_error_leaks(
    tmp_path: Path,
) -> None:
    facade, storage = _started_facade(tmp_path)
    try:
        with LocalWebServer(facade) as local:
            status, _, data = _request(f"{local.url}api/v1/dashboard?view=story")
            assert status == 200
            dashboard = json.loads(data)
            checkpoint_id = dashboard["controls"]["head"]["checkpoint_id"]
            assert isinstance(checkpoint_id, str)
            assets_by_role = {
                asset["role"]: asset for asset in dashboard["assets"]
            }
            placeholder = assets_by_role["placeholder"]
            final_asset = assets_by_role["final"]
            action_headers = {
                "X-Nining-Action-Token": local.action_token,
                "Origin": local.url.rstrip("/"),
            }

            status, _, data = _request(
                f"{local.url}api/v1/actions/review_accept",
                method="POST",
                payload={
                    "asset_id": final_asset["asset_id"],
                    "reason": "test hash requirement",
                    "expected_checkpoint_id": checkpoint_id,
                    "operation_id": "op-accept-without-hash",
                },
                extra_headers=action_headers,
            )
            assert status == 400
            assert "expected_sha256" in json.loads(data)["error"]["message"]

            status, _, data = _request(
                f"{local.url}api/v1/actions/review_accept",
                method="POST",
                payload={
                    "asset_id": final_asset["asset_id"],
                    "reason": "test stale hash",
                    "expected_checkpoint_id": checkpoint_id,
                    "expected_sha256": "0" * 64,
                    "operation_id": "op-accept-stale-hash",
                },
                extra_headers=action_headers,
            )
            assert status == 400
            assert "refresh" in json.loads(data)["error"]["message"].lower()

            status, _, data = _request(
                f"{local.url}api/v1/actions/review_accept",
                method="POST",
                payload={
                    "asset_id": placeholder["asset_id"],
                    "reason": "test strict placeholder gate",
                    "expected_checkpoint_id": checkpoint_id,
                    "expected_sha256": placeholder["sha256"],
                    "operation_id": "op-accept-placeholder",
                },
                extra_headers=action_headers,
            )
            assert status == 400
            message = json.loads(data)["error"]["message"].lower()
            assert "placeholder" in message
            assert "cannot be accepted" in message

            status, _, data = _request(
                f"{local.url}api/v1/actions/review_reject",
                method="POST",
                payload={
                    "asset_id": placeholder["asset_id"],
                    "reason": "reject the non-final placeholder",
                    "expected_checkpoint_id": checkpoint_id,
                    "operation_id": "op-reject-placeholder",
                },
                extra_headers=action_headers,
            )
            assert status == 200
            assert json.loads(data)["result"]["decision"]["action"] == "reject"

            fresh = facade.dashboard(view_mode="story")["controls"]["head"][
                "checkpoint_id"
            ]
            assert isinstance(fresh, str)

            def invalid_review(
                _asset_id: str,
                _action: str,
                _reason: str,
                _expected_sha256: str | None,
            ) -> dict[str, Any]:
                raise StorageValidationError("internal-key-marker private-path-marker")

            facade._review_provider = invalid_review
            status, _, data = _request(
                f"{local.url}api/v1/actions/review_reject",
                method="POST",
                payload={
                    "asset_id": placeholder["asset_id"],
                    "reason": "prove storage errors are redacted",
                    "expected_checkpoint_id": fresh,
                    "operation_id": "op-redacted-storage-error",
                },
                extra_headers=action_headers,
            )
            assert status == 400
            message = json.loads(data)["error"]["message"]
            assert message == "The review request is invalid. Refresh and correct the form before retrying."
            assert "internal-key-marker" not in message
            assert "private-path-marker" not in message
    finally:
        storage.close()


def test_empty_and_unavailable_provider_states_are_renderable_without_network() -> None:
    facade = RepositoryWebFacade(
        capability_provider=lambda: {
            "image": {
                "state": "unavailable",
                "provider": "configured-provider",
                "reason": "local configuration is incomplete",
                "final_eligible": False,
            },
            "voice": {
                "state": "degraded",
                "provider": "prompt_only",
                "reason": "local draft only",
                "final_eligible": False,
            },
        }
    )
    with LocalWebServer(facade) as local:
        status, _, data = _request(f"{local.url}api/v1/dashboard?view=story")
        payload = json.loads(data)
        assert status == 200
        assert payload["empty"] is True
        assert payload["capabilities"]["image"]["state"] == "unavailable"
        assert payload["capabilities"]["voice"]["state"] == "degraded"

        status, _, data = _request(f"{local.url}api/v1/scenes/missing?view=story")
        assert status == 404
        assert json.loads(data)["error"]["message"] == "Requested item was not found."


def test_asset_root_rejects_path_traversal_from_an_injected_asset_provider(
    tmp_path: Path,
) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    secret = tmp_path / "outside.txt"
    secret.write_text("must not be served", encoding="utf-8")
    facade = RepositoryWebFacade(
        asset_root=root,
        asset_provider=lambda _trigger: [
            {
                "asset_id": "safe-id",
                "relative_path": "../outside.txt",
                "media_type": "document",
                "role": "candidate",
                "final_eligible": False,
            }
        ],
    )
    assert facade.assets()[0]["preview_available"] is False
    assert facade.media("safe-id") is None


def test_static_browser_shell_has_accessibility_empty_and_narrow_layout_guards() -> (
    None
):
    html = (ASSETS / "index.html").read_text(encoding="utf-8")
    assert 'name="viewport"' in html
    assert "https://" not in html
    assert "http://" not in html
    if (ASSETS / "game.js").exists():
        css = (ASSETS / "game.css").read_text(encoding="utf-8")
        game = (ASSETS / "game.js").read_text(encoding="utf-8")
        application_source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(FRONTEND_SOURCE.rglob("*"))
            if path.is_file()
            and path.suffix in {".css", ".ts", ".tsx"}
            and "__tests__" not in path.parts
        )
        assert "__NINING_ACTION_TOKEN__" in html
        assert 'href="/assets/game.css"' in html
        assert 'src="/assets/game.js"' in html
        assert "./react-core.js" in game
        for name in ("react-core.js", "effects.js", "media-review.js", "god-charts.js"):
            assert f'src="/assets/{name}"' not in html
        for name in ("effects.js", "media-review.js", "god-charts.js"):
            assert f'import("./{name}")' in game
        assert "app.css" not in html
        assert "app.js" not in html
        assert "dangerouslySetInnerHTML" not in application_source
        assert "https://" not in application_source
        assert "http://" not in application_source
        assert 'href="#nining-main"' in application_source
        assert 'id="nining-main"' in application_source
        assert 'role="alert"' in application_source
        assert 'aria-live="polite"' in application_source
        assert "prefers-reduced-motion" in css
    else:
        css = (ASSETS / "app.css").read_text(encoding="utf-8")
        javascript = (ASSETS / "app.js").read_text(encoding="utf-8")
        assert 'href="#main-content"' in html
        assert 'role="alert"' in html
        assert 'aria-live="polite"' in html
        assert 'data-view-mode="story"' in html
        assert 'data-view-mode="god"' in html
        assert 'data-action="save"' not in html
        assert 'data-action="branch"' not in html
        assert "@media (max-width: 740px)" in css
        assert "prefers-reduced-motion" in css
        assert "No timeline entries are available." in javascript
        assert "No provider capability report is configured." in javascript
        assert "innerHTML" not in javascript
        assert "/api/v1/dashboard?" in javascript
        assert "payload.expected_checkpoint_id = checkpointId;" in javascript
        assert "payload.expected_sha256 = asset.sha256;" in javascript
