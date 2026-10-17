"""Loopback-only HTTP server for the Nining browser presentation."""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread
from typing import Any, Self
from urllib.parse import parse_qs, unquote, urlsplit

from ..storage.common import (
    StorageBusyError,
    StorageConflictError,
    StorageCorruptError,
    StorageError,
    StorageNotFoundError,
    StorageReadOnlyError,
    StorageUnavailableError,
    StorageValidationError,
)
from .facade import MUTATION_ACTIONS, MediaResource, WebDataProvider, WebFacadeError

_LOOPBACK_HOST = "127.0.0.1"
_MAX_BODY_BYTES = 1_048_576
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CONTENT_TYPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9!#$&^_.+-]+$")
_STATIC_DIR = Path(__file__).with_name("assets")
_STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/assets/game.css": ("game.css", "text/css; charset=utf-8"),
    "/assets/react-core.js": ("react-core.js", "application/javascript; charset=utf-8"),
    "/assets/game.js": ("game.js", "application/javascript; charset=utf-8"),
    "/assets/effects.js": ("effects.js", "application/javascript; charset=utf-8"),
    "/assets/media-review.js": ("media-review.js", "application/javascript; charset=utf-8"),
    "/assets/god-charts.js": ("god-charts.js", "application/javascript; charset=utf-8"),
}
# Pinned Radix/react-remove-scroll injects deterministic style elements while a
# modal is open.  Keep exact hashes so arbitrary inline style elements remain
# blocked; the real-browser E2E fails if an upstream upgrade changes them.
_MODAL_STYLE_SHA256_VALUES = (
    "sha256-kAApudxpTi9mfjlC9lC8ZaS9xFHU9/NLLbB173MU7SU=",
    "sha256-nzTgYzXYDNe6BAHiiI7NNlfK8n/auuOAhh2t92YvuXo=",
)
_MODAL_STYLE_CSP = " ".join(f"'{value}'" for value in _MODAL_STYLE_SHA256_VALUES)
_CSP = (
    "default-src 'self'; base-uri 'none'; connect-src 'self'; "
    "form-action 'self'; frame-ancestors 'none'; img-src 'self'; "
    "media-src 'self'; object-src 'none'; script-src 'self'; "
    f"style-src 'self'; style-src-elem 'self' {_MODAL_STYLE_CSP}; "
    "style-src-attr 'unsafe-inline'"
)
_STALE_HEAD_MESSAGE = "The playthrough changed; refresh the page and retry the action."


class _RequestError(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class LocalWebHTTPServer(ThreadingHTTPServer):
    """A server that refuses any non-loopback bind address."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, facade: WebDataProvider, *, host: str, port: int) -> None:
        if host != _LOOPBACK_HOST:
            raise ValueError("the web presentation may bind only to 127.0.0.1")
        self.facade = facade
        self.action_capability = secrets.token_urlsafe(32)
        self.mutation_lock = Lock()
        self.completed_operation_ids: set[str] = set()
        super().__init__((host, port), LocalWebRequestHandler)


class LocalWebRequestHandler(BaseHTTPRequestHandler):
    """Route only explicit local assets, APIs, and facade-authorized media."""

    server: LocalWebHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        """Avoid writing potentially user-supplied URL details into application logs."""

    def do_GET(self) -> None:
        self._dispatch(include_body=True)

    def do_HEAD(self) -> None:
        self._dispatch(include_body=False)

    def do_POST(self) -> None:
        action: str | None = None
        try:
            self._validate_host()
            self._validate_action_headers()
            parsed = urlsplit(self.path)
            if parsed.path.startswith("/api/v1/actions/"):
                action = self._route_identifier(
                    parsed.path.removeprefix("/api/v1/actions/"), "action"
                )
                payload = self._read_json_body()
                operation_id = payload.get("operation_id")
                if not isinstance(operation_id, str):
                    raise _RequestError(
                        HTTPStatus.BAD_REQUEST, "operation_id is required."
                    )
                self._validate_identifier(operation_id, "operation ID")
                with self.server.mutation_lock:
                    if operation_id in self.server.completed_operation_ids:
                        raise _RequestError(
                            HTTPStatus.CONFLICT,
                            "This operation was already completed.",
                        )
                    self._validate_mutation_precondition(action, payload)
                    result = self.server.facade.action(action, payload)
                    self.server.completed_operation_ids.add(operation_id)
                self._send_json(HTTPStatus.OK, result)
                return
            raise _RequestError(HTTPStatus.NOT_FOUND, "Route not found.")
        except _RequestError as exc:
            self._send_error_json(exc.status, exc.message)
        except StorageConflictError:
            self._send_error_json(HTTPStatus.CONFLICT, _STALE_HEAD_MESSAGE)
        except StorageValidationError as exc:
            self._send_error_json(
                HTTPStatus.BAD_REQUEST,
                self._storage_validation_message(action, exc),
            )
        except StorageNotFoundError:
            self._send_error_json(
                HTTPStatus.NOT_FOUND,
                "The requested local record is unavailable. Refresh and retry.",
            )
        except StorageReadOnlyError:
            self._send_error_json(
                HTTPStatus.CONFLICT,
                "The local playthrough is read-only. Reopen a writable session and retry.",
            )
        except StorageBusyError:
            self._send_error_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "The local playthrough is busy. Wait for the current action to finish and retry.",
            )
        except (StorageCorruptError, StorageUnavailableError, StorageError):
            self._send_error_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "The local playthrough data is unavailable. Reopen the session and retry.",
            )
        except KeyError:
            self._send_error_json(HTTPStatus.NOT_FOUND, "Requested item was not found.")
        except WebFacadeError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except (TypeError, ValueError):
            self._send_error_json(
                HTTPStatus.BAD_REQUEST, "Request could not be processed."
            )
        except Exception:  # noqa: BLE001 - provider implementations are injected.
            self._send_error_json(
                HTTPStatus.SERVICE_UNAVAILABLE, "Local data is unavailable."
            )

    def _dispatch(self, *, include_body: bool) -> None:
        try:
            self._validate_host()
            parsed = urlsplit(self.path)
            if parsed.path in _STATIC_FILES:
                self._send_static(parsed.path, include_body=include_body)
                return
            if parsed.path == "/api/v1/dashboard":
                query = self._query(parsed.query)
                view_mode = self._query_value(query, "view", default=None)
                trigger_id = self._query_value(query, "trigger", default=None)
                if trigger_id is not None:
                    self._validate_identifier(trigger_id, "trigger ID")
                self._send_json(
                    HTTPStatus.OK,
                    self.server.facade.dashboard(
                        view_mode=view_mode,
                        trigger_id=trigger_id,
                    ),
                    include_body=include_body,
                )
                return
            if parsed.path.startswith("/api/v1/scenes/"):
                trigger_id = self._route_identifier(
                    parsed.path.removeprefix("/api/v1/scenes/"), "trigger ID"
                )
                query = self._query(parsed.query)
                view_mode = self._query_value(query, "view", default="story")
                assert view_mode is not None
                self._send_json(
                    HTTPStatus.OK,
                    self.server.facade.scene(trigger_id, view_mode=view_mode),
                    include_body=include_body,
                )
                return
            if parsed.path == "/api/v1/capabilities":
                self._send_json(
                    HTTPStatus.OK,
                    self.server.facade.capabilities(),
                    include_body=include_body,
                )
                return
            if parsed.path == "/api/v1/assets":
                query = self._query(parsed.query)
                trigger_id = self._query_value(query, "trigger", default=None)
                if trigger_id is not None:
                    self._validate_identifier(trigger_id, "trigger ID")
                self._send_json(
                    HTTPStatus.OK,
                    {"assets": self.server.facade.assets(trigger_id=trigger_id)},
                    include_body=include_body,
                )
                return
            if parsed.path.startswith("/media/"):
                asset_id = self._route_identifier(
                    parsed.path.removeprefix("/media/"), "asset ID"
                )
                resource = self.server.facade.media(asset_id)
                if resource is None:
                    raise _RequestError(HTTPStatus.NOT_FOUND, "Media is not available.")
                self._send_media(resource, include_body=include_body)
                return
            raise _RequestError(HTTPStatus.NOT_FOUND, "Route not found.")
        except _RequestError as exc:
            self._send_error_json(exc.status, exc.message, include_body=include_body)
        except KeyError:
            self._send_error_json(
                HTTPStatus.NOT_FOUND,
                "Requested item was not found.",
                include_body=include_body,
            )
        except WebFacadeError as exc:
            self._send_error_json(
                HTTPStatus.BAD_REQUEST, str(exc), include_body=include_body
            )
        except (TypeError, ValueError):
            self._send_error_json(
                HTTPStatus.BAD_REQUEST,
                "Request could not be processed.",
                include_body=include_body,
            )
        except OSError:
            self._send_error_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "Requested local media is unavailable.",
                include_body=include_body,
            )
        except Exception:  # noqa: BLE001 - provider implementations are injected.
            self._send_error_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "Local data is unavailable.",
                include_body=include_body,
            )

    @staticmethod
    def _query(value: str) -> dict[str, list[str]]:
        return parse_qs(value, keep_blank_values=True, strict_parsing=False)

    @staticmethod
    def _query_value(
        query: Mapping[str, list[str]], key: str, *, default: str | None
    ) -> str | None:
        values = query.get(key)
        if values is None:
            return default
        if len(values) != 1:
            raise _RequestError(
                HTTPStatus.BAD_REQUEST, f"Only one {key} value is allowed."
            )
        return values[0]

    def _expected_origin(self) -> str:
        host = self.headers.get("Host", "")
        return f"http://{host}"

    def _validate_host(self) -> None:
        host = str(self.server.server_address[0])
        port = int(self.server.server_address[1])
        expected = f"{host}:{port}"
        if self.headers.get("Host") != expected:
            raise _RequestError(HTTPStatus.MISDIRECTED_REQUEST, "Invalid Host header.")

    def _validate_action_headers(self) -> None:
        token = self.headers.get("X-Nining-Action-Token", "")
        if not secrets.compare_digest(token, self.server.action_capability):
            raise _RequestError(HTTPStatus.FORBIDDEN, "Action token is missing or invalid.")
        origin = self.headers.get("Origin")
        if origin is not None and origin != self._expected_origin():
            raise _RequestError(HTTPStatus.FORBIDDEN, "Action origin is invalid.")
        fetch_site = self.headers.get("Sec-Fetch-Site")
        if fetch_site is not None and fetch_site != "same-origin":
            raise _RequestError(HTTPStatus.FORBIDDEN, "Cross-site actions are forbidden.")

    def _validate_mutation_precondition(
        self, action: str, payload: Mapping[str, Any]
    ) -> None:
        """Reject stale browser writes while the server mutation lock is held."""

        if action not in MUTATION_ACTIONS:
            return
        expected_checkpoint_id = payload.get("expected_checkpoint_id")
        if not isinstance(expected_checkpoint_id, str) or not expected_checkpoint_id:
            raise _RequestError(
                HTTPStatus.BAD_REQUEST, "expected_checkpoint_id is required."
            )
        self._validate_identifier(expected_checkpoint_id, "checkpoint ID")

        validator = getattr(
            self.server.facade, "validate_action_precondition", None
        )
        if callable(validator):
            validator(action, payload)
            return

        # Keep alternative presentation adapters fail-closed until they expose
        # the same lightweight head contract as RepositoryWebFacade.
        dashboard = self.server.facade.dashboard(view_mode=None)
        controls = dashboard.get("controls")
        head = controls.get("head") if isinstance(controls, Mapping) else None
        current_checkpoint_id = (
            head.get("checkpoint_id") if isinstance(head, Mapping) else None
        )
        if current_checkpoint_id != expected_checkpoint_id:
            raise _RequestError(HTTPStatus.CONFLICT, _STALE_HEAD_MESSAGE)

    @staticmethod
    def _storage_validation_message(
        action: str | None, error: StorageValidationError
    ) -> str:
        """Translate known review gates without exposing storage exception text."""

        detail = str(error)
        if action == "review_accept":
            if detail == "this asset role cannot be accepted":
                return (
                    "Placeholder, draft, and rejected assets cannot be accepted. "
                    "Choose a verified final candidate instead."
                )
            if detail == "asset hash changed; refresh review state":
                return "The asset changed since it was loaded. Refresh the review state and retry."
            if detail == "only media assets can be accepted":
                return "Only verified media assets can be accepted."
            if detail == "asset is not technically final eligible":
                return "This asset is not eligible for final acceptance."
            if detail.startswith("asset verification failed:"):
                return "The asset could not be verified. Refresh the review state and retry."

        labels = {
            "generate": "generation",
            "position": "position",
            "review_accept": "review",
            "review_reject": "review",
        }
        label = labels.get(action, "action") if action is not None else "action"
        return f"The {label} request is invalid. Refresh and correct the form before retrying."

    @staticmethod
    def _validate_identifier(value: str, label: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise _RequestError(HTTPStatus.BAD_REQUEST, f"Invalid {label}.")
        return value

    def _route_identifier(self, value: str, label: str) -> str:
        decoded = unquote(value)
        if "/" in decoded or "\\" in decoded:
            raise _RequestError(HTTPStatus.NOT_FOUND, "Route not found.")
        return self._validate_identifier(decoded, label)

    def _read_json_body(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().split(";", 1)[0].strip() == "application/json":
            raise _RequestError(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Expected a JSON request body."
            )
        length_text = self.headers.get("Content-Length")
        if length_text is None:
            raise _RequestError(
                HTTPStatus.LENGTH_REQUIRED, "Content-Length is required."
            )
        try:
            length = int(length_text)
        except ValueError as exc:
            raise _RequestError(
                HTTPStatus.BAD_REQUEST, "Invalid Content-Length."
            ) from exc
        if length < 0:
            raise _RequestError(HTTPStatus.BAD_REQUEST, "Invalid Content-Length.")
        if length > _MAX_BODY_BYTES:
            raise _RequestError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body is too large."
            )
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _RequestError(
                HTTPStatus.BAD_REQUEST, "Request body must be valid JSON."
            ) from exc
        if not isinstance(parsed, dict):
            raise _RequestError(
                HTTPStatus.BAD_REQUEST, "Request body must be a JSON object."
            )
        return parsed

    def _send_static(self, route: str, *, include_body: bool) -> None:
        filename, content_type = _STATIC_FILES[route]
        path = _STATIC_DIR / filename
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise _RequestError(
                HTTPStatus.SERVICE_UNAVAILABLE, "Local interface is unavailable."
            ) from exc
        if filename == "index.html":
            content = content.replace(
                b"__NINING_ACTION_TOKEN__",
                self.server.action_capability.encode("ascii"),
            )
        self._send_bytes(
            HTTPStatus.OK, content_type, content, include_body=include_body
        )

    def _send_media(self, resource: MediaResource, *, include_body: bool) -> None:
        if not resource.path.is_file() or resource.size_bytes < 0:
            raise _RequestError(HTTPStatus.NOT_FOUND, "Media is not available.")
        size_bytes = resource.path.stat().st_size
        content_type = (
            resource.content_type
            if _CONTENT_TYPE.fullmatch(resource.content_type)
            else "application/octet-stream"
        )
        self.send_response(HTTPStatus.OK)
        self._send_common_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size_bytes))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not include_body:
            return
        with resource.path.open("rb") as handle:
            while chunk := handle.read(64 * 1024):
                self.wfile.write(chunk)

    def _send_json(
        self, status: HTTPStatus, payload: Any, *, include_body: bool = True
    ) -> None:
        content = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self._send_bytes(
            status,
            "application/json; charset=utf-8",
            content,
            include_body=include_body,
        )

    def _send_error_json(
        self,
        status: HTTPStatus,
        message: str,
        *,
        include_body: bool = True,
    ) -> None:
        self._send_json(
            status,
            {"error": {"code": status.value, "message": message}},
            include_body=include_body,
        )

    def _send_bytes(
        self,
        status: HTTPStatus,
        content_type: str,
        content: bytes,
        *,
        include_body: bool,
    ) -> None:
        self.send_response(status)
        self._send_common_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if include_body:
            self.wfile.write(content)

    def _send_common_headers(self) -> None:
        self.send_header("Content-Security-Policy", _CSP)
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")


class LocalWebServer:
    """A convenient, explicitly loopback-only background server lifecycle."""

    def __init__(
        self,
        facade: WebDataProvider,
        *,
        host: str = _LOOPBACK_HOST,
        port: int = 0,
    ) -> None:
        self._server = create_server(facade, host=host, port=port)
        self._thread: Thread | None = None

    @property
    def host(self) -> str:
        return str(self._server.server_address[0])

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    @property
    def action_token(self) -> str:
        return self._server.action_capability

    def start(self) -> Self:
        if self._thread is None:
            self._thread = Thread(
                target=self._server.serve_forever,
                name="nining-local-web",
                daemon=True,
            )
            self._thread.start()
        return self

    def stop(self) -> None:
        if self._thread is not None:
            self._server.shutdown()
            self._thread.join(timeout=2)
            self._thread = None
        self._server.server_close()

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()


def create_server(
    facade: WebDataProvider,
    *,
    host: str = _LOOPBACK_HOST,
    port: int = 0,
) -> LocalWebHTTPServer:
    """Create, but do not start, a local server bound to exactly 127.0.0.1."""

    return LocalWebHTTPServer(facade, host=host, port=port)
