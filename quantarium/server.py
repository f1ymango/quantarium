"""Local HTTP server for the v1 JSON API and the bundled frontend.

The server listens on a loopback address only. It rejects requests whose
Host header is not that address (a guard against DNS rebinding), and it
requires a per-process token on every request that writes. The bundled
page receives the token when served; another website cannot read it. Reads
need no token: browsers do not let other origins read these responses,
because no CORS headers are sent.
"""

from __future__ import annotations

import ipaddress
import json
import mimetypes
import secrets
import sqlite3
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from . import strictjson
from .agents import AgentError
from .api import Api, ApiError
from .discovery import announce, withdraw
from .errors import describe

# Codes for errors that carry none of their own.
FALLBACK_CODES = {400: "invalid_request", 403: "forbidden", 404: "not_found", 413: "too_large",
                  415: "unsupported_media_type", 421: "unexpected_host", 500: "internal_error"}
from .workspace import Workspace

API_PREFIX = "/api/v1/"
TOKEN_HEADER = "X-Quantarium-Token"
TOKEN_PLACEHOLDER = "__QUANTARIUM_TOKEN__"
MAX_BODY = 1_048_576
# Seconds a client may stall while sending a request or reading a response,
# so a slow or idle connection cannot hold a server thread forever.
REQUEST_TIMEOUT = 30
CONTENT_TYPES = {
    "html": "text/html", "js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8",
    "json": "application/json", "svg": "image/svg+xml", "png": "image/png", "jpg": "image/jpeg", "ico": "image/x-icon",
    "md": "text/plain; charset=utf-8",
}


def _app_file(path: str) -> tuple[bytes, str] | None:
    """A file of the bundled frontend, or None."""
    relative = path.lstrip("/") or "index.html"
    if any(part in {"", ".", ".."} for part in relative.split("/")):
        return None
    resource = resources.files("quantarium").joinpath("assets", "app", *relative.split("/"))
    if not resource.is_file():
        return None
    # Fixed types: on Windows, mimetypes reads the registry, which can map
    # .js to text/plain, and browsers refuse to run modules served that way.
    suffix = relative.rsplit(".", 1)[-1].lower()
    content_type = CONTENT_TYPES.get(suffix) or mimetypes.guess_type(relative)[0] or "application/octet-stream"
    return resource.read_bytes(), content_type


class QuantariumServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, workspace: Workspace, host: str = "127.0.0.1", port: int = 8765) -> None:
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError("Quantarium serves only on a loopback address such as 127.0.0.1")
        super().__init__((host, port), Handler)
        self.api = Api(workspace)
        self.token = secrets.token_urlsafe(24)
        bound_port = self.server_address[1]
        names = {host, "localhost"} | ({"[::1]"} if host == "::1" else set())
        self.allowed_hosts = {f"{name}:{bound_port}" for name in names}
        self.allowed_origins = {f"http://{value}" for value in self.allowed_hosts}

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/"

    def server_close(self) -> None:
        self.api.close()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server: QuantariumServer
    protocol_version = "HTTP/1.1"
    timeout = REQUEST_TIMEOUT

    def log_message(self, format: str, *args) -> None:  # quieter than the default
        pass

    # Responses ---------------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if content_type.startswith("text/html"):
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                             "img-src 'self' data:; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str, code: str | None = None,
               hint: str | None = None) -> None:
        payload = {"error": message, "status": status, "code": code or FALLBACK_CODES.get(status, "error")}
        if hint:
            payload["hint"] = hint
        self._json(status, payload)

    # Guards ------------------------------------------------------------

    def _host_allowed(self) -> bool:
        if self.headers.get("Host") not in self.server.allowed_hosts:
            self._error(HTTPStatus.MISDIRECTED_REQUEST, "Unexpected Host header")
            return False
        return True

    def _write_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is not None and origin not in self.server.allowed_origins:
            self._error(HTTPStatus.FORBIDDEN, "Cross-origin write refused")
            return False
        supplied = self.headers.get(TOKEN_HEADER, "")
        if not secrets.compare_digest(supplied, self.server.token):
            self._error(HTTPStatus.FORBIDDEN, f"Missing or wrong {TOKEN_HEADER}")
            return False
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Send application/json")
            return False
        return True

    def _bearer(self) -> str | None:
        """The agent token, when the request carries one."""
        header = self.headers.get("Authorization") or ""
        scheme, _, value = header.partition(" ")
        return value.strip() if scheme.lower() == "bearer" else None

    def _agent_origin_allowed(self) -> bool:
        # Agent clients send no Origin; refuse one from a web page all the same.
        origin = self.headers.get("Origin")
        if origin is not None and origin not in self.server.allowed_origins:
            self._error(HTTPStatus.FORBIDDEN, "Agent requests may not come from a web page")
            return False
        return True

    def _dispatch(self, call) -> None:
        try:
            status, payload = call()
        except ApiError as exc:
            self._error(exc.status, str(exc), exc.code, exc.hint)
        except AgentError as exc:
            self._error(exc.status, str(exc), exc.code)
        except FileNotFoundError as exc:
            self._error(HTTPStatus.NOT_FOUND, f"Not found: {exc}", *describe(exc))
        except PermissionError as exc:
            self._error(HTTPStatus.FORBIDDEN, str(exc))
        except (ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            code, hint = describe(exc)
            message = str(exc) if code else f"{type(exc).__name__}: {exc}"
            self._error(HTTPStatus.BAD_REQUEST, message, code or "invalid_request", hint)
        except Exception as exc:  # never leak a traceback to the client
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"Internal error: {type(exc).__name__}")
        else:
            self._json(status, payload)

    # Methods -----------------------------------------------------------

    def do_GET(self) -> None:
        if not self._host_allowed():
            return
        url = urlsplit(self.path)
        if url.path.startswith(API_PREFIX):
            params = dict(parse_qsl(url.query))
            route = url.path[len(API_PREFIX):].strip("/")
            token = self._bearer()
            if token is not None:
                if not self._agent_origin_allowed():
                    return
                self._dispatch(lambda: (HTTPStatus.OK,
                                        self.server.api.agent_request(token, "get", route, params)))
                return
            self._dispatch(lambda: (HTTPStatus.OK, self.server.api.get(route, params)))
            return
        found = _app_file(url.path)
        if found is None:
            self._error(HTTPStatus.NOT_FOUND, "Not found")
            return
        body, content_type = found
        if content_type == "text/html":
            body = body.replace(TOKEN_PLACEHOLDER.encode(), self.server.token.encode())
            content_type = "text/html; charset=utf-8"
        self._send(HTTPStatus.OK, body, content_type)

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            self.close_connection = True
            self._error(HTTPStatus.BAD_REQUEST, "Invalid Content-Length")
            return
        if length > MAX_BODY:
            self.close_connection = True
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body too large")
            return
        # Read the body before any refusal, so a kept-alive connection does
        # not parse it as the next request.
        raw = self.rfile.read(length) if length else b""
        token = self._bearer()
        if not self._host_allowed():
            return
        # An agent token replaces the browser token. A website cannot send one:
        # an Authorization header needs a CORS preflight, which this server
        # never grants.
        if token is not None:
            if not self._agent_origin_allowed():
                return
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Send application/json")
                return
        elif not self._write_allowed():
            return
        url = urlsplit(self.path)
        endpoint = url.path.rstrip("/")[len(API_PREFIX):] if url.path.startswith(API_PREFIX) else ""

        if endpoint not in {"jobs", "commands"}:
            self._error(HTTPStatus.NOT_FOUND, "POST is accepted at jobs and commands")
            return
        field = "action" if endpoint == "jobs" else "command"

        def submit():
            try:
                body = strictjson.loads(raw or b"{}")
            except ValueError as exc:
                raise ApiError(400, f"Invalid request body: {exc}", "invalid_request") from None
            if not isinstance(body, dict) or not isinstance(body.get(field), str):
                raise ApiError(400, f"Body must be {{\"{field}\": ..., \"params\": {{...}}}}")
            params = body.get("params", {})
            if not isinstance(params, dict):
                raise ApiError(400, "params must be an object")
            api = self.server.api
            key = body.get("idempotency_key") if endpoint == "jobs" else None
            if token is not None:
                kind = "job" if endpoint == "jobs" else "command"
                result = api.agent_request(token, kind, body[field], params, key)
                return (HTTPStatus.ACCEPTED if kind == "job" else HTTPStatus.OK), result
            if endpoint == "jobs":
                return HTTPStatus.ACCEPTED, api.submit(body[field], params, key)
            return HTTPStatus.OK, api.command(body[field], params)

        self._dispatch(submit)


def serve(workspace_root: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    server = QuantariumServer(Workspace(workspace_root), host, port)
    root = server.api.workspace.root
    # Lets `quantarium mcp` find this server instead of starting a second writer.
    announce(root, server.url)
    print(f"Quantarium {server.url} (workspace {root})")
    print(f"Write token for scripts: {server.token}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        withdraw(root)
        server.server_close()
