from __future__ import annotations

import hashlib
import json
import math
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from fleet_ops.errors import DomainError

_TRANSITIONS = {
    "pending": {"assigned", "cancelled"},
    "assigned": {"in_transit", "cancelled"},
    "in_transit": {"delivered", "cancelled"},
    "delivered": set(),
    "cancelled": set(),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class FleetService:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS drivers (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('available', 'assigned')),
                    vehicle TEXT,
                    max_weight_kg REAL,
                    max_volume_m3 REAL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY,
                    pickup TEXT NOT NULL,
                    dropoff TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('pending', 'assigned', 'in_transit', 'delivered', 'cancelled')
                    ),
                    driver_id TEXT REFERENCES drivers(id),
                    cargo_description TEXT,
                    cargo_weight_kg REAL,
                    cargo_volume_m3 REAL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS order_events (
                    id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL REFERENCES orders(id),
                    event_type TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    driver_id TEXT REFERENCES drivers(id),
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS order_events_order_id_created_at
                    ON order_events(order_id, created_at);
                CREATE TABLE IF NOT EXISTS idempotency_records (
                    idempotency_key TEXT PRIMARY KEY,
                    operation TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS driver_location_tokens (
                    driver_id TEXT PRIMARY KEY REFERENCES drivers(id),
                    salt BLOB NOT NULL,
                    token_hash BLOB NOT NULL,
                    issued_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS customer_tracking_tokens (
                    order_id TEXT PRIMARY KEY REFERENCES orders(id),
                    salt BLOB NOT NULL,
                    token_hash BLOB NOT NULL,
                    issued_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS driver_locations (
                    id TEXT PRIMARY KEY,
                    driver_id TEXT NOT NULL REFERENCES drivers(id),
                    latitude REAL NOT NULL CHECK (latitude BETWEEN -90 AND 90),
                    longitude REAL NOT NULL CHECK (longitude BETWEEN -180 AND 180),
                    accuracy_m REAL,
                    recorded_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS driver_locations_driver_recorded
                    ON driver_locations(driver_id, recorded_at);
                CREATE TABLE IF NOT EXISTS workflow_templates (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    steps_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS order_workflow_tasks (
                    id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL REFERENCES orders(id),
                    label TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    completed_at TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE (order_id, position)
                );
                CREATE INDEX IF NOT EXISTS order_workflow_tasks_order_position
                    ON order_workflow_tasks(order_id, position);
                """
            )
            self._ensure_columns(
                connection,
                "drivers",
                {
                    "vehicle": "TEXT",
                    "max_weight_kg": "REAL",
                    "max_volume_m3": "REAL",
                },
            )
            self._ensure_columns(
                connection,
                "orders",
                {
                    "cargo_description": "TEXT",
                    "cargo_weight_kg": "REAL",
                    "cargo_volume_m3": "REAL",
                    "pickup_latitude": "REAL",
                    "pickup_longitude": "REAL",
                    "dropoff_latitude": "REAL",
                    "dropoff_longitude": "REAL",
                    "workflow_name": "TEXT",
                },
            )
            retention_cutoff = (
                datetime.now(timezone.utc) - timedelta(days=30)
            ).isoformat(timespec="seconds")
            connection.execute(
                "DELETE FROM driver_locations WHERE recorded_at < ?",
                (retention_cutoff,),
            )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _ensure_columns(
        connection: sqlite3.Connection, table: str, columns: dict[str, str]
    ) -> None:
        existing = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for name, data_type in columns.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {data_type}")

    def _get_idempotent_response(
        self,
        connection: sqlite3.Connection,
        key: str,
        operation: str,
        request: dict[str, Any],
    ) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT operation, request_hash, response_json FROM idempotency_records "
            "WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        request_hash = hashlib.sha256(_canonical_json(request).encode("utf-8")).hexdigest()
        if row["operation"] != operation or row["request_hash"] != request_hash:
            raise DomainError(
                "idempotency_key_reused",
                "Idempotency-Key was already used with a different operation or payload.",
                409,
            )
        return json.loads(row["response_json"])

    def _save_idempotency_response(
        self,
        connection: sqlite3.Connection,
        key: str,
        operation: str,
        request: dict[str, Any],
        response: dict[str, Any],
    ) -> None:
        request_hash = hashlib.sha256(_canonical_json(request).encode("utf-8")).hexdigest()
        connection.execute(
            "INSERT INTO idempotency_records "
            "(idempotency_key, operation, request_hash, response_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, operation, request_hash, _canonical_json(response), _now()),
        )

    @staticmethod
    def _require_text(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise DomainError("invalid_input", f"{field} must be a non-empty string.")
        return value.strip()

    @staticmethod
    def _optional_text(value: Any, field: str) -> str | None:
        if value is None or value == "":
            return None
        return FleetService._require_text(value, field)

    @staticmethod
    def _optional_positive_number(value: Any, field: str) -> float | None:
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            raise DomainError("invalid_input", f"{field} must be a positive number.")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise DomainError("invalid_input", f"{field} must be a positive number.") from None
        if not math.isfinite(number) or number <= 0 or number > 1_000_000_000:
            raise DomainError("invalid_input", f"{field} must be a positive number.")
        return number

    @staticmethod
    def _coordinate(value: Any, field: str, minimum: float, maximum: float) -> float | None:
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            raise DomainError("invalid_input", f"{field} must be between {minimum} and {maximum}.")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise DomainError(
                "invalid_input", f"{field} must be between {minimum} and {maximum}."
            ) from None
        if not math.isfinite(number) or not minimum <= number <= maximum:
            raise DomainError("invalid_input", f"{field} must be between {minimum} and {maximum}.")
        return number

    @staticmethod
    def _require_key(key: str) -> str:
        if not isinstance(key, str) or not key.strip() or len(key) > 200:
            raise DomainError(
                "invalid_idempotency_key",
                "Idempotency-Key must contain 1 to 200 characters.",
            )
        return key.strip()

    @staticmethod
    def _order_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "pickup": row["pickup"],
            "dropoff": row["dropoff"],
            "status": row["status"],
            "driver_id": row["driver_id"],
            "cargo_description": row["cargo_description"],
            "cargo_weight_kg": row["cargo_weight_kg"],
            "cargo_volume_m3": row["cargo_volume_m3"],
            "pickup_latitude": row["pickup_latitude"],
            "pickup_longitude": row["pickup_longitude"],
            "dropoff_latitude": row["dropoff_latitude"],
            "dropoff_longitude": row["dropoff_longitude"],
            "workflow_name": row["workflow_name"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _driver_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "status": row["status"],
            "vehicle": row["vehicle"],
            "max_weight_kg": row["max_weight_kg"],
            "max_volume_m3": row["max_volume_m3"],
            "tracking_enabled": bool(row["tracking_enabled"]),
            "created_at": row["created_at"],
        }

    def create_driver(
        self,
        name: Any,
        idempotency_key: str,
        vehicle: Any = None,
        max_weight_kg: Any = None,
        max_volume_m3: Any = None,
    ) -> dict[str, Any]:
        name = self._require_text(name, "name")
        vehicle = self._optional_text(vehicle, "vehicle")
        max_weight_kg = self._optional_positive_number(max_weight_kg, "max_weight_kg")
        max_volume_m3 = self._optional_positive_number(max_volume_m3, "max_volume_m3")
        key = self._require_key(idempotency_key)
        request = {
            "max_volume_m3": max_volume_m3,
            "max_weight_kg": max_weight_kg,
            "name": name,
            "vehicle": vehicle,
        }
        with self._transaction() as connection:
            replay = self._get_idempotent_response(connection, key, "create_driver", request)
            if replay is not None:
                return replay
            driver_id = str(uuid.uuid4())
            created_at = _now()
            connection.execute(
                "INSERT INTO drivers "
                "(id, name, status, vehicle, max_weight_kg, max_volume_m3, created_at) "
                "VALUES (?, ?, 'available', ?, ?, ?, ?)",
                (driver_id, name, vehicle, max_weight_kg, max_volume_m3, created_at),
            )
            response = {
                "id": driver_id,
                "name": name,
                "status": "available",
                "vehicle": vehicle,
                "max_weight_kg": max_weight_kg,
                "max_volume_m3": max_volume_m3,
                "created_at": created_at,
            }
            self._save_idempotency_response(connection, key, "create_driver", request, response)
            return response

    def issue_driver_location_token(self, driver_id: str) -> dict[str, Any]:
        token = f"{driver_id}.{secrets.token_urlsafe(32)}"
        salt = secrets.token_bytes(16)
        token_hash = hashlib.pbkdf2_hmac("sha256", token.encode("utf-8"), salt, 180_000)
        issued_at = _now()
        with self._transaction() as connection:
            driver = connection.execute("SELECT id FROM drivers WHERE id = ?", (driver_id,)).fetchone()
            if driver is None:
                raise DomainError("driver_not_found", "Driver was not found.", 404)
            connection.execute(
                "INSERT INTO driver_location_tokens (driver_id, salt, token_hash, issued_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(driver_id) DO UPDATE SET "
                "salt = excluded.salt, token_hash = excluded.token_hash, issued_at = excluded.issued_at",
                (driver_id, salt, token_hash, issued_at),
            )
        return {"driver_id": driver_id, "token": token, "issued_at": issued_at}

    def issue_customer_tracking_token(self, order_id: str) -> dict[str, Any]:
        secret = secrets.token_urlsafe(32)
        token = f"{order_id}.{secret}"
        salt = secrets.token_bytes(16)
        token_hash = hashlib.pbkdf2_hmac("sha256", token.encode("utf-8"), salt, 180_000)
        issued_at = _now()
        with self._transaction() as connection:
            order = connection.execute(
                "SELECT id FROM orders WHERE id = ?", (order_id,)
            ).fetchone()
            if order is None:
                raise DomainError("order_not_found", "Order was not found.", 404)
            connection.execute(
                "INSERT INTO customer_tracking_tokens (order_id, salt, token_hash, issued_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(order_id) DO UPDATE SET "
                "salt = excluded.salt, token_hash = excluded.token_hash, issued_at = excluded.issued_at",
                (order_id, salt, token_hash, issued_at),
            )
        return {"order_id": order_id, "token": token, "issued_at": issued_at}

    def revoke_customer_tracking_token(self, order_id: str) -> dict[str, Any]:
        with self._transaction() as connection:
            order = connection.execute(
                "SELECT id FROM orders WHERE id = ?", (order_id,)
            ).fetchone()
            if order is None:
                raise DomainError("order_not_found", "Order was not found.", 404)
            connection.execute(
                "DELETE FROM customer_tracking_tokens WHERE order_id = ?", (order_id,)
            )
        return {"order_id": order_id, "revoked": True}

    def get_customer_tracking(self, token: Any) -> dict[str, Any]:
        if not isinstance(token, str) or len(token) > 128 or token.count(".") != 1:
            raise DomainError("tracking_link_not_found", "Tracking link is invalid or revoked.", 404)
        order_id, secret = token.split(".", 1)
        if not order_id or not secret:
            raise DomainError("tracking_link_not_found", "Tracking link is invalid or revoked.", 404)
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT o.*, t.salt, t.token_hash FROM orders o "
                "JOIN customer_tracking_tokens t ON t.order_id = o.id WHERE o.id = ?",
                (order_id,),
            ).fetchone()
            if row is None:
                raise DomainError(
                    "tracking_link_not_found", "Tracking link is invalid or revoked.", 404
                )
            actual_hash = hashlib.pbkdf2_hmac(
                "sha256", token.encode("utf-8"), row["salt"], 180_000
            )
            if not secrets.compare_digest(actual_hash, row["token_hash"]):
                raise DomainError(
                    "tracking_link_not_found", "Tracking link is invalid or revoked.", 404
                )

            public_events = [
                {"status": event["to_status"], "updated_at": event["created_at"]}
                for event in connection.execute(
                    "SELECT to_status, created_at FROM order_events "
                    "WHERE order_id = ? ORDER BY created_at, rowid",
                    (order_id,),
                ).fetchall()
            ]
            latest_location = None
            if row["status"] == "in_transit":
                locations = self._order_locations(connection, order_id, row["driver_id"])
                if locations:
                    location = locations[0]
                    age_seconds = (
                        datetime.now(timezone.utc)
                        - datetime.fromisoformat(location["recorded_at"])
                    ).total_seconds()
                    if 0 <= age_seconds <= 120:
                        latest_location = {
                            "latitude": location["latitude"],
                            "longitude": location["longitude"],
                            "recorded_at": location["recorded_at"],
                        }
            return {
                "reference": order_id[:8].upper(),
                "pickup": row["pickup"],
                "dropoff": row["dropoff"],
                "status": row["status"],
                "updated_at": row["updated_at"],
                "pickup_latitude": row["pickup_latitude"],
                "pickup_longitude": row["pickup_longitude"],
                "dropoff_latitude": row["dropoff_latitude"],
                "dropoff_longitude": row["dropoff_longitude"],
                "latest_location": latest_location,
                "events": public_events,
            }
        finally:
            connection.close()

    def record_driver_location(
        self,
        token: Any,
        latitude: Any,
        longitude: Any,
        accuracy_m: Any = None,
    ) -> dict[str, Any]:
        if not isinstance(token, str) or len(token) > 128 or "." not in token:
            raise DomainError("invalid_driver_token", "A valid driver token is required.", 401)
        driver_id, secret = token.split(".", 1)
        if not driver_id or not secret:
            raise DomainError("invalid_driver_token", "A valid driver token is required.", 401)
        latitude = self._coordinate(latitude, "latitude", -90, 90)
        longitude = self._coordinate(longitude, "longitude", -180, 180)
        if latitude is None or longitude is None:
            raise DomainError("invalid_input", "Latitude and longitude are required.")
        accuracy_m = self._coordinate(accuracy_m, "accuracy_m", 0, 10_000)
        with self._transaction() as connection:
            credential = connection.execute(
                "SELECT salt, token_hash FROM driver_location_tokens WHERE driver_id = ?",
                (driver_id,),
            ).fetchone()
            if credential is None:
                raise DomainError("invalid_driver_token", "A valid driver token is required.", 401)
            actual_hash = hashlib.pbkdf2_hmac(
                "sha256", token.encode("utf-8"), credential["salt"], 180_000
            )
            if not secrets.compare_digest(actual_hash, credential["token_hash"]):
                raise DomainError("invalid_driver_token", "A valid driver token is required.", 401)
            assignment = connection.execute(
                "SELECT id FROM orders WHERE driver_id = ? AND status = 'in_transit'",
                (driver_id,),
            ).fetchone()
            if assignment is None:
                raise DomainError(
                    "driver_not_in_transit",
                    "Location sharing is available only while a shipment is in transit.",
                    409,
                )
            recorded_at = _now()
            previous = connection.execute(
                "SELECT recorded_at FROM driver_locations WHERE driver_id = ? "
                "ORDER BY recorded_at DESC, rowid DESC LIMIT 1",
                (driver_id,),
            ).fetchone()
            if previous and (
                datetime.fromisoformat(recorded_at)
                - datetime.fromisoformat(previous["recorded_at"])
            ).total_seconds() < 5:
                raise DomainError(
                    "location_rate_limited",
                    "Driver location updates must be at least five seconds apart.",
                    429,
                )
            retention_cutoff = (
                datetime.now(timezone.utc) - timedelta(days=30)
            ).isoformat(timespec="seconds")
            connection.execute(
                "DELETE FROM driver_locations WHERE recorded_at < ?",
                (retention_cutoff,),
            )
            location_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO driver_locations "
                "(id, driver_id, latitude, longitude, accuracy_m, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (location_id, driver_id, latitude, longitude, accuracy_m, recorded_at),
            )
        return {
            "id": location_id,
            "latitude": latitude,
            "longitude": longitude,
            "accuracy_m": accuracy_m,
            "recorded_at": recorded_at,
        }

    @staticmethod
    def _order_locations(
        connection: sqlite3.Connection, order_id: str, driver_id: str | None
    ) -> list[dict[str, Any]]:
        if driver_id is None:
            return []
        start = connection.execute(
            "SELECT created_at FROM order_events WHERE order_id = ? AND to_status = 'in_transit' "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (order_id,),
        ).fetchone()
        if start is None:
            return []
        end = connection.execute(
            "SELECT created_at FROM order_events WHERE order_id = ? "
            "AND to_status IN ('delivered', 'cancelled') AND created_at >= ? "
            "ORDER BY created_at, rowid LIMIT 1",
            (order_id, start["created_at"]),
        ).fetchone()
        end_clause = "AND recorded_at <= ?" if end else ""
        parameters = [driver_id, start["created_at"]]
        if end:
            parameters.append(end["created_at"])
        rows = connection.execute(
            "SELECT latitude, longitude, accuracy_m, recorded_at FROM driver_locations "
            "WHERE driver_id = ? AND recorded_at >= ? "
            f"{end_clause} ORDER BY recorded_at DESC, rowid DESC LIMIT 100",
            parameters,
        ).fetchall()
        return [dict(row) for row in rows]

    def create_order(
        self,
        pickup: Any,
        dropoff: Any,
        idempotency_key: str,
        cargo_description: Any = None,
        cargo_weight_kg: Any = None,
        cargo_volume_m3: Any = None,
        pickup_latitude: Any = None,
        pickup_longitude: Any = None,
        dropoff_latitude: Any = None,
        dropoff_longitude: Any = None,
        workflow_id: Any = None,
    ) -> dict[str, Any]:
        pickup = self._require_text(pickup, "pickup")
        dropoff = self._require_text(dropoff, "dropoff")
        cargo_description = self._optional_text(cargo_description, "cargo_description")
        cargo_weight_kg = self._optional_positive_number(cargo_weight_kg, "cargo_weight_kg")
        cargo_volume_m3 = self._optional_positive_number(cargo_volume_m3, "cargo_volume_m3")
        pickup_latitude = self._coordinate(pickup_latitude, "pickup_latitude", -90, 90)
        pickup_longitude = self._coordinate(pickup_longitude, "pickup_longitude", -180, 180)
        dropoff_latitude = self._coordinate(dropoff_latitude, "dropoff_latitude", -90, 90)
        dropoff_longitude = self._coordinate(dropoff_longitude, "dropoff_longitude", -180, 180)
        if (pickup_latitude is None) != (pickup_longitude is None):
            raise DomainError("invalid_input", "Pickup latitude and longitude must be provided together.")
        if (dropoff_latitude is None) != (dropoff_longitude is None):
            raise DomainError("invalid_input", "Drop-off latitude and longitude must be provided together.")
        workflow_id = self._optional_text(workflow_id, "workflow_id")
        key = self._require_key(idempotency_key)
        request = {
            "cargo_description": cargo_description,
            "cargo_volume_m3": cargo_volume_m3,
            "cargo_weight_kg": cargo_weight_kg,
            "dropoff": dropoff,
            "pickup": pickup,
            "pickup_latitude": pickup_latitude,
            "pickup_longitude": pickup_longitude,
            "dropoff_latitude": dropoff_latitude,
            "dropoff_longitude": dropoff_longitude,
            "workflow_id": workflow_id,
        }
        with self._transaction() as connection:
            replay = self._get_idempotent_response(connection, key, "create_order", request)
            if replay is not None:
                return replay
            workflow = None
            if workflow_id is not None:
                workflow = connection.execute(
                    "SELECT name, steps_json FROM workflow_templates WHERE id = ?",
                    (workflow_id,),
                ).fetchone()
                if workflow is None:
                    raise DomainError("workflow_not_found", "Workflow template was not found.", 404)
            order_id = str(uuid.uuid4())
            created_at = _now()
            connection.execute(
                "INSERT INTO orders "
                "(id, pickup, dropoff, status, cargo_description, cargo_weight_kg, "
                "cargo_volume_m3, created_at, updated_at) "
                "VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?)",
                (
                    order_id,
                    pickup,
                    dropoff,
                    cargo_description,
                    cargo_weight_kg,
                    cargo_volume_m3,
                    created_at,
                    created_at,
                ),
            )
            connection.execute(
                "UPDATE orders SET pickup_latitude = ?, pickup_longitude = ?, "
                "dropoff_latitude = ?, dropoff_longitude = ?, workflow_name = ? WHERE id = ?",
                (
                    pickup_latitude,
                    pickup_longitude,
                    dropoff_latitude,
                    dropoff_longitude,
                    workflow["name"] if workflow else None,
                    order_id,
                ),
            )
            if workflow:
                for position, label in enumerate(json.loads(workflow["steps_json"])):
                    connection.execute(
                        "INSERT INTO order_workflow_tasks "
                        "(id, order_id, label, position, created_at) VALUES (?, ?, ?, ?, ?)",
                        (str(uuid.uuid4()), order_id, label, position, created_at),
                    )
            connection.execute(
                "INSERT INTO order_events "
                "(id, order_id, event_type, from_status, to_status, created_at) "
                "VALUES (?, ?, 'order_created', NULL, 'pending', ?)",
                (str(uuid.uuid4()), order_id, created_at),
            )
            response = {
                "id": order_id,
                "pickup": pickup,
                "dropoff": dropoff,
                "status": "pending",
                "driver_id": None,
                "cargo_description": cargo_description,
                "cargo_weight_kg": cargo_weight_kg,
                "cargo_volume_m3": cargo_volume_m3,
                "pickup_latitude": pickup_latitude,
                "pickup_longitude": pickup_longitude,
                "dropoff_latitude": dropoff_latitude,
                "dropoff_longitude": dropoff_longitude,
                "workflow_name": workflow["name"] if workflow else None,
                "created_at": created_at,
                "updated_at": created_at,
            }
            self._save_idempotency_response(connection, key, "create_order", request, response)
            return response

    def dispatch_order(
        self, order_id: str, driver_id: Any, idempotency_key: str
    ) -> dict[str, Any]:
        driver_id = self._require_text(driver_id, "driver_id")
        key = self._require_key(idempotency_key)
        request = {"driver_id": driver_id, "order_id": order_id}
        with self._transaction() as connection:
            replay = self._get_idempotent_response(connection, key, "dispatch_order", request)
            if replay is not None:
                return replay
            order = connection.execute(
                "SELECT * FROM orders WHERE id = ?", (order_id,)
            ).fetchone()
            if order is None:
                raise DomainError("order_not_found", "Order was not found.", 404)
            if order["status"] != "pending":
                raise DomainError(
                    "invalid_order_state",
                    "Only pending orders can be dispatched.",
                    409,
                )
            driver = connection.execute(
                "SELECT * FROM drivers WHERE id = ?", (driver_id,)
            ).fetchone()
            if driver is None:
                raise DomainError("driver_not_found", "Driver was not found.", 404)
            if driver["status"] != "available":
                raise DomainError(
                    "driver_unavailable",
                    "Driver is already assigned to an active order.",
                    409,
                )
            if (
                order["cargo_weight_kg"] is not None
                and driver["max_weight_kg"] is not None
                and order["cargo_weight_kg"] > driver["max_weight_kg"]
            ):
                raise DomainError(
                    "driver_capacity_exceeded",
                    "Cargo weight exceeds this driver's vehicle capacity.",
                    409,
                )
            if (
                order["cargo_volume_m3"] is not None
                and driver["max_volume_m3"] is not None
                and order["cargo_volume_m3"] > driver["max_volume_m3"]
            ):
                raise DomainError(
                    "driver_capacity_exceeded",
                    "Cargo volume exceeds this driver's vehicle capacity.",
                    409,
                )
            timestamp = _now()
            connection.execute(
                "UPDATE orders SET status = 'assigned', driver_id = ?, updated_at = ? WHERE id = ?",
                (driver_id, timestamp, order_id),
            )
            connection.execute(
                "UPDATE drivers SET status = 'assigned' WHERE id = ?", (driver_id,)
            )
            connection.execute(
                "INSERT INTO order_events "
                "(id, order_id, event_type, from_status, to_status, driver_id, created_at) "
                "VALUES (?, ?, 'order_dispatched', 'pending', 'assigned', ?, ?)",
                (str(uuid.uuid4()), order_id, driver_id, timestamp),
            )
            updated_order = connection.execute(
                "SELECT * FROM orders WHERE id = ?", (order_id,)
            ).fetchone()
            response = self._order_dict(updated_order)
            self._save_idempotency_response(
                connection, key, "dispatch_order", request, response
            )
            return response

    def transition_order(
        self, order_id: str, next_status: Any, idempotency_key: str
    ) -> dict[str, Any]:
        next_status = self._require_text(next_status, "status")
        key = self._require_key(idempotency_key)
        request = {"order_id": order_id, "status": next_status}
        with self._transaction() as connection:
            replay = self._get_idempotent_response(
                connection, key, "transition_order", request
            )
            if replay is not None:
                return replay
            order = connection.execute(
                "SELECT * FROM orders WHERE id = ?", (order_id,)
            ).fetchone()
            if order is None:
                raise DomainError("order_not_found", "Order was not found.", 404)
            current_status = order["status"]
            if next_status not in _TRANSITIONS[current_status]:
                raise DomainError(
                    "invalid_order_transition",
                    f"Order cannot transition from {current_status} to {next_status}.",
                    409,
                )
            timestamp = _now()
            connection.execute(
                "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
                (next_status, timestamp, order_id),
            )
            if next_status in {"delivered", "cancelled"} and order["driver_id"]:
                connection.execute(
                    "UPDATE drivers SET status = 'available' WHERE id = ?",
                    (order["driver_id"],),
                )
            connection.execute(
                "INSERT INTO order_events "
                "(id, order_id, event_type, from_status, to_status, driver_id, created_at) "
                "VALUES (?, ?, 'order_status_changed', ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    order_id,
                    current_status,
                    next_status,
                    order["driver_id"],
                    timestamp,
                ),
            )
            updated_order = connection.execute(
                "SELECT * FROM orders WHERE id = ?", (order_id,)
            ).fetchone()
            response = self._order_dict(updated_order)
            self._save_idempotency_response(
                connection, key, "transition_order", request, response
            )
            return response

    @staticmethod
    def _workflow_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "steps": json.loads(row["steps_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def create_workflow(
        self, name: Any, description: Any, steps: Any, idempotency_key: str
    ) -> dict[str, Any]:
        name, description, normalized_steps = self._workflow_input(name, description, steps)
        key = self._require_key(idempotency_key)
        request = {"name": name, "description": description, "steps": normalized_steps}
        with self._transaction() as connection:
            replay = self._get_idempotent_response(connection, key, "create_workflow", request)
            if replay is not None:
                return replay
            workflow_id = str(uuid.uuid4())
            created_at = _now()
            connection.execute(
                "INSERT INTO workflow_templates "
                "(id, name, description, steps_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    workflow_id,
                    name,
                    description,
                    json.dumps(normalized_steps, separators=(",", ":")),
                    created_at,
                    created_at,
                ),
            )
            response = {
                "id": workflow_id,
                "name": name,
                "description": description,
                "steps": normalized_steps,
                "created_at": created_at,
                "updated_at": created_at,
            }
            self._save_idempotency_response(
                connection, key, "create_workflow", request, response
            )
            return response

    @staticmethod
    def _workflow_input(name: Any, description: Any, steps: Any) -> tuple[str, str | None, list[str]]:
        name = FleetService._require_text(name, "name")
        if len(name) > 100:
            raise DomainError("invalid_input", "Workflow name must be at most 100 characters.")
        description = FleetService._optional_text(description, "description")
        if description is not None and len(description) > 500:
            raise DomainError("invalid_input", "Workflow description must be at most 500 characters.")
        if not isinstance(steps, list) or not 1 <= len(steps) <= 20:
            raise DomainError("invalid_input", "A workflow must contain 1 to 20 checklist steps.")
        normalized_steps = []
        for index, step in enumerate(steps, start=1):
            label = FleetService._require_text(step, f"step {index}")
            if len(label) > 160:
                raise DomainError("invalid_input", f"Workflow step {index} must be at most 160 characters.")
            normalized_steps.append(label)
        return name, description, normalized_steps

    def update_workflow(
        self,
        workflow_id: str,
        name: Any,
        description: Any,
        steps: Any,
        idempotency_key: str,
    ) -> dict[str, Any]:
        name, description, normalized_steps = self._workflow_input(name, description, steps)
        key = self._require_key(idempotency_key)
        request = {
            "workflow_id": workflow_id,
            "name": name,
            "description": description,
            "steps": normalized_steps,
        }
        with self._transaction() as connection:
            replay = self._get_idempotent_response(connection, key, "update_workflow", request)
            if replay is not None:
                return replay
            existing = connection.execute(
                "SELECT created_at FROM workflow_templates WHERE id = ?", (workflow_id,)
            ).fetchone()
            if existing is None:
                raise DomainError("workflow_not_found", "Workflow template was not found.", 404)
            updated_at = _now()
            connection.execute(
                "UPDATE workflow_templates SET name = ?, description = ?, steps_json = ?, "
                "updated_at = ? WHERE id = ?",
                (
                    name,
                    description,
                    json.dumps(normalized_steps, separators=(",", ":")),
                    updated_at,
                    workflow_id,
                ),
            )
            response = {
                "id": workflow_id,
                "name": name,
                "description": description,
                "steps": normalized_steps,
                "created_at": existing["created_at"],
                "updated_at": updated_at,
            }
            self._save_idempotency_response(
                connection, key, "update_workflow", request, response
            )
            return response

    def delete_workflow(self, workflow_id: str, idempotency_key: str) -> dict[str, Any]:
        key = self._require_key(idempotency_key)
        request = {"workflow_id": workflow_id}
        with self._transaction() as connection:
            replay = self._get_idempotent_response(
                connection, key, "delete_workflow", request
            )
            if replay is not None:
                return replay
            cursor = connection.execute(
                "DELETE FROM workflow_templates WHERE id = ?", (workflow_id,)
            )
            if cursor.rowcount == 0:
                raise DomainError("workflow_not_found", "Workflow template was not found.", 404)
            response = {"id": workflow_id, "deleted": True}
            self._save_idempotency_response(
                connection, key, "delete_workflow", request, response
            )
            return response

    def list_workflows(self) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM workflow_templates ORDER BY name COLLATE NOCASE, id"
            ).fetchall()
            return [self._workflow_dict(row) for row in rows]
        finally:
            connection.close()

    def complete_workflow_task(
        self, order_id: str, task_id: str, idempotency_key: str
    ) -> dict[str, Any]:
        key = self._require_key(idempotency_key)
        request = {"order_id": order_id, "task_id": task_id}
        with self._transaction() as connection:
            replay = self._get_idempotent_response(
                connection, key, "complete_workflow_task", request
            )
            if replay is not None:
                return replay
            order = connection.execute(
                "SELECT status, driver_id FROM orders WHERE id = ?", (order_id,)
            ).fetchone()
            if order is None:
                raise DomainError("order_not_found", "Order was not found.", 404)
            task = connection.execute(
                "SELECT * FROM order_workflow_tasks WHERE id = ? AND order_id = ?",
                (task_id, order_id),
            ).fetchone()
            if task is None:
                raise DomainError("workflow_task_not_found", "Workflow task was not found.", 404)
            if task["completed_at"] is not None:
                raise DomainError("workflow_task_already_completed", "Workflow task is already complete.", 409)
            completed_at = _now()
            connection.execute(
                "UPDATE order_workflow_tasks SET completed_at = ? WHERE id = ?",
                (completed_at, task_id),
            )
            connection.execute(
                "INSERT INTO order_events "
                "(id, order_id, event_type, from_status, to_status, driver_id, created_at) "
                "VALUES (?, ?, 'workflow_task_completed', ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    order_id,
                    order["status"],
                    order["status"],
                    order["driver_id"],
                    completed_at,
                ),
            )
            response = {
                "id": task_id,
                "label": task["label"],
                "completed_at": completed_at,
            }
            self._save_idempotency_response(
                connection, key, "complete_workflow_task", request, response
            )
            return response

    def _workflow_tasks(
        self, connection: sqlite3.Connection, order_id: str
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT id, label, position, completed_at FROM order_workflow_tasks "
            "WHERE order_id = ? ORDER BY position",
            (order_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_order(self, order_id: str) -> dict[str, Any]:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM orders WHERE id = ?", (order_id,)
            ).fetchone()
            if row is None:
                raise DomainError("order_not_found", "Order was not found.", 404)
            result = self._order_dict(row)
            result["customer_tracking_enabled"] = connection.execute(
                "SELECT 1 FROM customer_tracking_tokens WHERE order_id = ?",
                (order_id,),
            ).fetchone() is not None
            result["workflow_tasks"] = self._workflow_tasks(connection, order_id)
            locations = self._order_locations(connection, order_id, row["driver_id"])
            result["locations"] = locations
            result["latest_location"] = (
                locations[0] if locations and row["status"] == "in_transit" else None
            )
            result["events"] = [
                dict(event)
                for event in connection.execute(
                    "SELECT id, event_type, from_status, to_status, driver_id, created_at "
                    "FROM order_events WHERE order_id = ? ORDER BY created_at, rowid",
                    (order_id,),
                ).fetchall()
            ]
            return result
        finally:
            connection.close()

    def list_orders(self) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT o.*, EXISTS(SELECT 1 FROM customer_tracking_tokens t "
                "WHERE t.order_id = o.id) AS customer_tracking_enabled "
                "FROM orders o ORDER BY o.created_at DESC, o.id"
            ).fetchall()
            results = []
            for row in rows:
                order = self._order_dict(row)
                order["customer_tracking_enabled"] = bool(row["customer_tracking_enabled"])
                order["workflow_tasks"] = self._workflow_tasks(connection, row["id"])
                locations = self._order_locations(connection, row["id"], row["driver_id"])
                order["latest_location"] = (
                    locations[0] if locations and row["status"] == "in_transit" else None
                )
                results.append(order)
            return results
        finally:
            connection.close()

    def list_drivers(self) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT d.*, EXISTS(SELECT 1 FROM driver_location_tokens t "
                "WHERE t.driver_id = d.id) AS tracking_enabled "
                "FROM drivers d ORDER BY created_at DESC, id"
            ).fetchall()
            return [self._driver_dict(row) for row in rows]
        finally:
            connection.close()
