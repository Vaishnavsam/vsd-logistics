from __future__ import annotations

import json
import os
import ipaddress
import secrets
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http import HTTPStatus
from mimetypes import guess_type
from typing import Any
from urllib.parse import unquote, urlsplit

from fleet_ops.errors import DomainError
from fleet_ops.auth import AuthManager
from fleet_ops.service import FleetService


class FleetRequestHandler(BaseHTTPRequestHandler):
    service: FleetService
    auth: AuthManager
    web_root = Path(__file__).resolve().parents[2] / "web"
    web_files = {
        "/": "index.html",
        "/product": "product.html",
        "/app.js": "app.js",
        "/styles.css": "styles.css",
        "/product.css": "product.css",
        "/product.js": "product.js",
        "/product.js": "product.js",
        "/driver": "driver.html",
        "/driver.js": "driver.js",
        "/driver.css": "driver.css",
        "/customer.js": "customer.js",
        "/customer.css": "customer.css",
    }
    _tracking_limit_lock = threading.Lock()
    _tracking_requests: dict[str, list[float]] = {}

    def _send_json(self, status: int, body: Any) -> None:
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(encoded)

    def _send_web_file(self, path: str) -> bool:
        filename = self.web_files.get(path)
        if filename is None:
            return False
        content = (self.web_root / filename).read_bytes()
        content_type = guess_type(filename)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)
        return True

    def _send_customer_page(self) -> None:
        filename = "customer.html"
        content = (self.web_root / filename).read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def _limit_customer_tracking(self) -> None:
        now = time.monotonic()
        client = self.client_address[0]
        handler_type = type(self)
        with handler_type._tracking_limit_lock:
            requests = [
                timestamp
                for timestamp in handler_type._tracking_requests.get(client, [])
                if now - timestamp < 60
            ]
            if len(requests) >= 30:
                raise DomainError(
                    "tracking_rate_limited",
                    "Too many tracking requests. Please try again shortly.",
                    429,
                )
            requests.append(now)
            handler_type._tracking_requests[client] = requests
            if len(handler_type._tracking_requests) > 1024:
                handler_type._tracking_requests = {
                    address: timestamps
                    for address, timestamps in handler_type._tracking_requests.items()
                    if timestamps and now - timestamps[-1] < 60
                }

    def _read_json(self) -> dict[str, Any]:
        content_length = self.headers.get("Content-Length")
        if content_length is None:
            raise DomainError("invalid_json", "A JSON request body is required.")
        try:
            length = int(content_length)
            if length < 0 or length > 1_000_000:
                raise ValueError
            body = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            raise DomainError("invalid_json", "Request body must be valid JSON.") from None
        if not isinstance(body, dict):
            raise DomainError("invalid_json", "Request body must be a JSON object.")
        return body

    def _idempotency_key(self) -> str:
        key = self.headers.get("Idempotency-Key")
        if key is None:
            raise DomainError(
                "idempotency_key_required",
                "Idempotency-Key header is required for write requests.",
            )
        return key

    def _bearer_token(self) -> str | None:
        authorization = self.headers.get("Authorization", "")
        scheme, separator, token = authorization.partition(" ")
        if separator and scheme.lower() == "bearer" and token.strip():
            return token.strip()
        return None

    def _handle(self) -> None:
        path = unquote(urlsplit(self.path).path)
        parts = [part for part in path.split("/") if part]
        if path == "/health" and self.command == "GET":
            self._send_json(200, {"status": "ok"})
            return
        if self.command == "GET" and path == "/track":
            self._send_customer_page()
            return
        if self.command == "GET" and self._send_web_file(path):
            return
        if (
            self.command == "GET"
            and path == "/customer/tracking"
        ):
            self._limit_customer_tracking()
            self._send_json(
                200,
                self.service.get_customer_tracking(
                    self.headers.get("X-Customer-Tracking-Token")
                ),
            )
            return
        if path == "/auth/login" and self.command == "POST":
            body = self._read_json()
            password = body.get("password")
            if not isinstance(password, str):
                raise DomainError("invalid_credentials", "Password is required.", 401)
            session = self.auth.login(password, self.client_address[0])
            self._send_json(
                200,
                {
                    "access_token": session.token,
                    "token_type": "Bearer",
                    "expires_in": 8 * 60 * 60,
                },
            )
            return
        if path == "/auth/session" and self.command == "GET":
            session = self.auth.authenticate(self._bearer_token())
            self._send_json(200, {"authenticated": True, "expires_at": session.expires_at})
            return
        if path == "/auth/logout" and self.command == "POST":
            self.auth.logout(self._bearer_token())
            self._send_json(200, {"status": "signed_out"})
            return
        if path == "/driver/location" and self.command == "POST":
            body = self._read_json()
            location = self.service.record_driver_location(
                self.headers.get("X-Driver-Token"),
                body.get("latitude"),
                body.get("longitude"),
                body.get("accuracy_m"),
            )
            self._send_json(201, location)
            return
        self.auth.authenticate(self._bearer_token())
        if self.command == "GET":
            if path == "/orders":
                self._send_json(200, {"orders": self.service.list_orders()})
            elif len(parts) == 2 and parts[0] == "orders":
                self._send_json(200, self.service.get_order(parts[1]))
            elif path == "/drivers":
                self._send_json(200, {"drivers": self.service.list_drivers()})
            elif path == "/workflows":
                self._send_json(200, {"workflows": self.service.list_workflows()})
            else:
                self._send_json(404, {"error": {"code": "not_found", "message": "Route was not found."}})
            return
        if self.command == "DELETE" and len(parts) == 3 and parts[0] == "orders" and parts[2] == "customer-tracking-token":
            self._send_json(
                200,
                self.service.revoke_customer_tracking_token(parts[1]),
            )
            return
        if self.command != "POST":
            self._send_json(
                405,
                {"error": {"code": "method_not_allowed", "message": "Method is not supported."}},
            )
            return
        if len(parts) == 3 and parts[0] == "orders" and parts[2] == "customer-tracking-token":
            self._send_json(201, self.service.issue_customer_tracking_token(parts[1]))
            return
        if len(parts) == 3 and parts[0] == "drivers" and parts[2] == "tracking-token":
            self._send_json(201, self.service.issue_driver_location_token(parts[1]))
            return
        if (
            len(parts) == 5
            and parts[0] == "orders"
            and parts[2] == "workflow-tasks"
            and parts[4] == "complete"
        ):
            self._read_json()
            key = self._idempotency_key()
            self._send_json(
                200,
                self.service.complete_workflow_task(parts[1], parts[3], key),
            )
            return
        if len(parts) == 3 and parts[0] == "workflows" and parts[2] in {"delete", "update"}:
            body = self._read_json()
            key = self._idempotency_key()
            if parts[2] == "delete":
                self._send_json(
                    200,
                    self.service.delete_workflow(parts[1], key),
                )
            else:
                self._send_json(
                    200,
                    self.service.update_workflow(
                        parts[1],
                        body.get("name"),
                        body.get("description"),
                        body.get("steps"),
                        key,
                    ),
                )
            return
        body = self._read_json()
        key = self._idempotency_key()
        if path == "/workflows":
            self._send_json(
                201,
                self.service.create_workflow(
                    body.get("name"),
                    body.get("description"),
                    body.get("steps"),
                    key,
                ),
            )
        elif path == "/drivers":
            self._send_json(
                201,
                self.service.create_driver(
                    body.get("name"),
                    key,
                    body.get("vehicle"),
                    body.get("max_weight_kg"),
                    body.get("max_volume_m3"),
                ),
            )
        elif path == "/orders":
            self._send_json(
                201,
                self.service.create_order(
                    body.get("pickup"),
                    body.get("dropoff"),
                    key,
                    body.get("cargo_description"),
                    body.get("cargo_weight_kg"),
                    body.get("cargo_volume_m3"),
                    body.get("pickup_latitude"),
                    body.get("pickup_longitude"),
                    body.get("dropoff_latitude"),
                    body.get("dropoff_longitude"),
                    body.get("workflow_id"),
                ),
            )
        elif len(parts) == 3 and parts[0] == "orders" and parts[2] == "dispatch":
            self._send_json(
                200,
                self.service.dispatch_order(parts[1], body.get("driver_id"), key),
            )
        elif len(parts) == 3 and parts[0] == "orders" and parts[2] == "transitions":
            self._send_json(
                200,
                self.service.transition_order(parts[1], body.get("status"), key),
            )
        else:
            self._send_json(404, {"error": {"code": "not_found", "message": "Route was not found."}})

    def _dispatch(self) -> None:
        try:
            self._handle()
        except DomainError as error:
            self._send_json(
                error.status_code,
                {"error": {"code": error.code, "message": error.message}},
            )

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def do_DELETE(self) -> None:
        self._dispatch()

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    database_path = os.environ.get("FLEET_OPS_DB", "fleet-ops.sqlite3")
    host = os.environ.get("FLEET_OPS_HOST", "127.0.0.1")
    port = int(os.environ.get("FLEET_OPS_PORT", "8080"))
    admin_password = os.environ.get("FLEET_OPS_ADMIN_PASSWORD")
    if admin_password is None:
        raise RuntimeError(
            "Set FLEET_OPS_ADMIN_PASSWORD to a unique password of at least 12 characters."
        )
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = host.lower() == "localhost"
    if not is_loopback:
        raise RuntimeError(
            "The built-in HTTP server only supports loopback binding. "
            "Use an HTTPS reverse proxy for remote access."
        )
    FleetRequestHandler.auth = AuthManager(admin_password)
    FleetRequestHandler.service = FleetService(database_path)
    server = ThreadingHTTPServer((host, port), FleetRequestHandler)
    print(f"VSD Logistics web app and API listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
