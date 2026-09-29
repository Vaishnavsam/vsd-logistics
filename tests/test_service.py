import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
from datetime import datetime, timedelta, timezone

from fleet_ops.errors import DomainError
from fleet_ops.service import FleetService


class FleetServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "fleet.sqlite3"
        self.service = FleetService(self.database_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def create_driver(self, key: str = "driver-1") -> dict:
        return self.service.create_driver("Avery", key)

    def create_order(self, key: str = "order-1") -> dict:
        return self.service.create_order("Warehouse", "Customer", key)

    def test_idempotent_order_creation_returns_original_order(self) -> None:
        first = self.create_order()
        second = self.create_order()
        self.assertEqual(first, second)
        self.assertEqual(len(self.service.list_orders()), 1)
        self.assertEqual(len(self.service.get_order(first["id"])["events"]), 1)

    def test_reusing_key_with_different_payload_conflicts(self) -> None:
        self.create_order()
        with self.assertRaises(DomainError) as raised:
            self.service.create_order("Different", "Customer", "order-1")
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(len(self.service.list_orders()), 1)

    def test_idempotency_checks_cargo_and_vehicle_metadata(self) -> None:
        self.service.create_order(
            "Warehouse", "Customer", "cargo-key", cargo_weight_kg=100
        )
        with self.assertRaises(DomainError) as order_error:
            self.service.create_order(
                "Warehouse", "Customer", "cargo-key", cargo_weight_kg=101
            )
        self.assertEqual(order_error.exception.status_code, 409)

        self.service.create_driver("Avery", "vehicle-key", "Van", max_weight_kg=500)
        with self.assertRaises(DomainError) as driver_error:
            self.service.create_driver("Avery", "vehicle-key", "Van", max_weight_kg=501)
        self.assertEqual(driver_error.exception.status_code, 409)

    def test_dispatch_and_delivery_update_order_driver_and_event_history(self) -> None:
        driver = self.create_driver()
        order = self.create_order()

        assigned = self.service.dispatch_order(order["id"], driver["id"], "dispatch-1")
        self.assertEqual(assigned["status"], "assigned")
        self.assertEqual(assigned["driver_id"], driver["id"])

        self.service.transition_order(order["id"], "in_transit", "start-1")
        delivered = self.service.transition_order(order["id"], "delivered", "deliver-1")
        self.assertEqual(delivered["status"], "delivered")
        self.assertEqual(self.service.list_drivers()[0]["status"], "available")
        self.assertEqual(
            [event["to_status"] for event in self.service.get_order(order["id"])["events"]],
            ["pending", "assigned", "in_transit", "delivered"],
        )

    def test_driver_cannot_be_assigned_to_two_active_orders(self) -> None:
        driver = self.create_driver()
        first = self.create_order("order-1")
        second = self.create_order("order-2")
        self.service.dispatch_order(first["id"], driver["id"], "dispatch-1")

        with self.assertRaises(DomainError) as raised:
            self.service.dispatch_order(second["id"], driver["id"], "dispatch-2")
        self.assertEqual(raised.exception.code, "driver_unavailable")
        self.assertEqual(self.service.get_order(second["id"])["status"], "pending")

    def test_dispatch_rejects_cargo_over_vehicle_weight_capacity(self) -> None:
        driver = self.service.create_driver(
            "Avery", "driver-limits", "Van 12", max_weight_kg=500
        )
        order = self.service.create_order(
            "Warehouse", "Customer", "heavy-order", cargo_weight_kg=750
        )

        with self.assertRaises(DomainError) as raised:
            self.service.dispatch_order(order["id"], driver["id"], "dispatch-heavy")

        self.assertEqual(raised.exception.code, "driver_capacity_exceeded")
        self.assertEqual(self.service.get_order(order["id"])["status"], "pending")
        self.assertEqual(self.service.list_drivers()[0]["status"], "available")

    def test_dispatch_rejects_cargo_over_vehicle_volume_capacity(self) -> None:
        driver = self.service.create_driver(
            "Avery", "driver-limits", "Van 12", max_volume_m3=8
        )
        order = self.service.create_order(
            "Warehouse", "Customer", "large-order", cargo_volume_m3=9
        )

        with self.assertRaises(DomainError) as raised:
            self.service.dispatch_order(order["id"], driver["id"], "dispatch-large")

        self.assertEqual(raised.exception.code, "driver_capacity_exceeded")

    def test_capacity_measurements_must_be_positive_finite_numbers(self) -> None:
        for invalid_value in (0, -1, "not-a-number", float("inf")):
            with self.subTest(value=invalid_value):
                with self.assertRaises(DomainError):
                    self.service.create_order(
                        "Warehouse",
                        "Customer",
                        f"invalid-{invalid_value}",
                        cargo_weight_kg=invalid_value,
                    )

    def test_order_coordinates_are_validated_and_idempotent(self) -> None:
        order = self.service.create_order(
            "Warehouse",
            "Customer",
            "coordinate-order",
            pickup_latitude=40.7128,
            pickup_longitude=-74.006,
            dropoff_latitude=40.758,
            dropoff_longitude=-73.9855,
        )
        self.assertEqual(order["pickup_latitude"], 40.7128)
        self.assertEqual(
            self.service.create_order(
                "Warehouse",
                "Customer",
                "coordinate-order",
                pickup_latitude=40.7128,
                pickup_longitude=-74.006,
                dropoff_latitude=40.758,
                dropoff_longitude=-73.9855,
            ),
            order,
        )
        with self.assertRaises(DomainError):
            self.service.create_order(
                "Warehouse", "Customer", "invalid-coordinate", pickup_latitude=91
            )
        with self.assertRaises(DomainError):
            self.service.create_order(
                "Warehouse", "Customer", "incomplete-coordinate", pickup_latitude=40
            )

    def test_customer_tracking_token_is_hashed_rotatable_and_revocable(self) -> None:
        order = self.service.create_order(
            "Warehouse", "Customer", "customer-link-order",
            cargo_description="Private cargo", cargo_weight_kg=25,
            pickup_latitude=40.7, pickup_longitude=-74,
        )
        first = self.service.issue_customer_tracking_token(order["id"])
        self.assertTrue(self.service.get_order(order["id"])["customer_tracking_enabled"])
        public = self.service.get_customer_tracking(first["token"])
        self.assertEqual(public["pickup"], "Warehouse")
        self.assertEqual(public["status"], "pending")
        self.assertEqual(public["pickup_latitude"], 40.7)
        self.assertEqual(
            set(public),
            {
                "reference", "pickup", "dropoff", "status", "updated_at",
                "pickup_latitude", "pickup_longitude", "dropoff_latitude",
                "dropoff_longitude", "latest_location", "events",
            },
        )
        self.assertNotIn("Private cargo", str(public))
        connection = sqlite3.connect(self.database_path)
        try:
            stored = connection.execute(
                "SELECT salt, token_hash FROM customer_tracking_tokens WHERE order_id = ?",
                (order["id"],),
            ).fetchone()
        finally:
            connection.close()
        self.assertNotIn(first["token"].encode(), stored[1])

        second = self.service.issue_customer_tracking_token(order["id"])
        with self.assertRaises(DomainError) as rotated:
            self.service.get_customer_tracking(first["token"])
        self.assertEqual(rotated.exception.status_code, 404)
        self.assertEqual(self.service.get_customer_tracking(second["token"])["status"], "pending")

        self.service.revoke_customer_tracking_token(order["id"])
        self.assertFalse(self.service.get_order(order["id"])["customer_tracking_enabled"])
        with self.assertRaises(DomainError) as revoked:
            self.service.get_customer_tracking(second["token"])
        self.assertEqual(revoked.exception.status_code, 404)

    def test_customer_tracking_only_returns_a_fresh_in_transit_fix(self) -> None:
        driver = self.create_driver()
        order = self.create_order()
        self.service.dispatch_order(order["id"], driver["id"], "customer-dispatch")
        self.service.transition_order(order["id"], "in_transit", "customer-start")
        driver_token = self.service.issue_driver_location_token(driver["id"])["token"]
        self.service.record_driver_location(driver_token, 40.7, -74)
        customer_token = self.service.issue_customer_tracking_token(order["id"])["token"]
        public = self.service.get_customer_tracking(customer_token)
        self.assertEqual(public["latest_location"]["latitude"], 40.7)
        self.assertEqual(
            [event["status"] for event in public["events"]],
            ["pending", "assigned", "in_transit"],
        )
        self.assertNotIn("driver_id", str(public))

        connection = sqlite3.connect(self.database_path)
        try:
            stale_at = (datetime.now(timezone.utc) - timedelta(seconds=121)).isoformat()
            connection.execute(
                "UPDATE driver_locations SET recorded_at = ?", (stale_at,)
            )
            connection.commit()
        finally:
            connection.close()
        self.assertIsNone(
            self.service.get_customer_tracking(customer_token)["latest_location"]
        )

        self.service.transition_order(order["id"], "delivered", "customer-delivered")
        delivered = self.service.get_customer_tracking(customer_token)
        self.assertEqual(delivered["status"], "delivered")
        self.assertIsNone(delivered["latest_location"])

    def test_custom_workflow_is_snapshotted_and_tasks_are_audited(self) -> None:
        workflow = self.service.create_workflow(
            "Cold-chain",
            "Temperature controlled shipments",
            ["Verify seal", "Record temperature", "Confirm recipient"],
            "workflow-cold-chain",
        )
        self.assertEqual(len(self.service.list_workflows()), 1)
        order = self.service.create_order(
            "Cold storage", "Clinic", "workflow-order", workflow_id=workflow["id"]
        )
        tasks = self.service.get_order(order["id"])["workflow_tasks"]
        self.assertEqual([task["label"] for task in tasks], workflow["steps"])
        completed = self.service.complete_workflow_task(
            order["id"], tasks[0]["id"], "workflow-task-complete"
        )
        self.assertEqual(completed["label"], "Verify seal")
        self.assertEqual(
            self.service.complete_workflow_task(
                order["id"], tasks[0]["id"], "workflow-task-complete"
            ),
            completed,
        )
        detail = self.service.get_order(order["id"])
        self.assertIsNotNone(detail["workflow_tasks"][0]["completed_at"])
        self.assertEqual(detail["status"], "pending")
        self.assertEqual(detail["events"][-1]["event_type"], "workflow_task_completed")

    def test_workflow_validation_and_foreign_task_protection(self) -> None:
        with self.assertRaises(DomainError):
            self.service.create_workflow("Empty", None, [], "empty-workflow")
        workflow = self.service.create_workflow(
            "Standard", None, ["Verify pickup"], "valid-workflow"
        )
        first = self.service.create_order(
            "A", "B", "workflow-first", workflow_id=workflow["id"]
        )
        second = self.service.create_order(
            "C", "D", "workflow-second", workflow_id=workflow["id"]
        )
        task_id = self.service.get_order(first["id"])["workflow_tasks"][0]["id"]
        with self.assertRaises(DomainError) as raised:
            self.service.complete_workflow_task(second["id"], task_id, "wrong-order-task")
        self.assertEqual(raised.exception.code, "workflow_task_not_found")

    def test_workflow_update_and_delete_keep_existing_order_snapshot(self) -> None:
        workflow = self.service.create_workflow(
            "Standard", None, ["Confirm pickup"], "workflow-edit-create"
        )
        order = self.service.create_order(
            "A", "B", "workflow-edit-order", workflow_id=workflow["id"]
        )
        updated = self.service.update_workflow(
            workflow["id"],
            "Updated standard",
            "New description",
            ["Confirm pickup", "Capture proof"],
            "workflow-edit-update",
        )
        self.assertEqual(updated["name"], "Updated standard")
        self.assertEqual(len(self.service.get_order(order["id"])["workflow_tasks"]), 1)
        deleted = self.service.delete_workflow(workflow["id"], "workflow-edit-delete")
        self.assertTrue(deleted["deleted"])
        self.assertEqual(self.service.list_workflows(), [])
        self.assertEqual(
            self.service.get_order(order["id"])["workflow_tasks"][0]["label"],
            "Confirm pickup",
        )

    def test_driver_location_tokens_are_scoped_rotatable_and_append_history(self) -> None:
        driver = self.create_driver()
        order = self.create_order()
        first_token = self.service.issue_driver_location_token(driver["id"])["token"]
        with self.assertRaises(DomainError) as pre_transit:
            self.service.record_driver_location(first_token, 40.7, -74.0, 15)
        self.assertEqual(pre_transit.exception.code, "driver_not_in_transit")

        self.service.dispatch_order(order["id"], driver["id"], "location-dispatch")
        self.service.transition_order(order["id"], "in_transit", "location-start")
        fix = self.service.record_driver_location(first_token, 40.7, -74.0, 15)
        detail = self.service.get_order(order["id"])
        self.assertEqual(detail["latest_location"]["latitude"], 40.7)
        self.assertEqual(detail["locations"][0]["accuracy_m"], 15)

        new_token = self.service.issue_driver_location_token(driver["id"])["token"]
        with self.assertRaises(DomainError) as revoked:
            self.service.record_driver_location(first_token, 40.8, -74.1, 20)
        self.assertEqual(revoked.exception.code, "invalid_driver_token")

        connection = sqlite3.connect(self.database_path)
        stored = connection.execute(
            "SELECT token_hash FROM driver_location_tokens WHERE driver_id = ?",
            (driver["id"],),
        ).fetchone()[0]
        connection.close()
        self.assertNotIn(new_token.encode(), stored)
        self.assertTrue(fix["id"])

    def test_driver_location_reports_are_rate_limited(self) -> None:
        driver = self.create_driver()
        order = self.create_order()
        token = self.service.issue_driver_location_token(driver["id"])["token"]
        self.service.dispatch_order(order["id"], driver["id"], "rate-limit-dispatch")
        self.service.transition_order(order["id"], "in_transit", "rate-limit-start")
        self.service.record_driver_location(token, 40.7, -74.0)

        with self.assertRaises(DomainError) as raised:
            self.service.record_driver_location(token, 40.8, -74.1)
        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(raised.exception.code, "location_rate_limited")

        connection = sqlite3.connect(self.database_path)
        now = datetime.now(timezone.utc)
        old_time = (now - timedelta(seconds=6)).isoformat()
        started_at = (now - timedelta(seconds=20)).isoformat()
        connection.execute(
            "UPDATE driver_locations SET recorded_at = ? WHERE driver_id = ?",
            (old_time, driver["id"]),
        )
        connection.execute(
            "UPDATE order_events SET created_at = ? WHERE order_id = ? AND to_status = 'in_transit'",
            (started_at, order["id"]),
        )
        connection.commit()
        connection.close()
        self.service.record_driver_location(token, 40.8, -74.1)
        self.assertEqual(len(self.service.get_order(order["id"])["locations"]), 2)

    def test_driver_location_rejects_invalid_coordinates(self) -> None:
        driver = self.create_driver()
        token = self.service.issue_driver_location_token(driver["id"])["token"]
        order = self.create_order()
        self.service.dispatch_order(order["id"], driver["id"], "invalid-gps-dispatch")
        self.service.transition_order(order["id"], "in_transit", "invalid-gps-start")
        for latitude, longitude in ((91, 0), (0, 181), (float("nan"), 0)):
            with self.subTest(latitude=latitude, longitude=longitude):
                with self.assertRaises(DomainError):
                    self.service.record_driver_location(token, latitude, longitude)

    def test_existing_database_gets_optional_tracking_columns(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy.sqlite3"
        connection = sqlite3.connect(legacy_path)
        connection.executescript(
            """
            CREATE TABLE drivers (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE orders (
                id TEXT PRIMARY KEY, pickup TEXT NOT NULL, dropoff TEXT NOT NULL,
                status TEXT NOT NULL, driver_id TEXT, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE order_events (
                id TEXT PRIMARY KEY, order_id TEXT NOT NULL,
                event_type TEXT NOT NULL, from_status TEXT,
                to_status TEXT NOT NULL, driver_id TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE idempotency_records (
                idempotency_key TEXT PRIMARY KEY, operation TEXT NOT NULL,
                request_hash TEXT NOT NULL, response_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            INSERT INTO drivers VALUES ('driver-existing', 'Jordan', 'available', '2026-01-01');
            INSERT INTO orders VALUES (
                'order-existing', 'Warehouse', 'Customer', 'pending', NULL,
                '2026-01-01', '2026-01-01'
            );
            """
        )
        connection.commit()
        connection.close()

        migrated_service = FleetService(legacy_path)
        self.assertEqual(migrated_service.get_order("order-existing")["cargo_weight_kg"], None)
        self.assertEqual(migrated_service.list_drivers()[0]["vehicle"], None)

    def test_concurrent_dispatches_assign_driver_to_only_one_order(self) -> None:
        driver = self.create_driver()
        orders = [self.create_order("order-1"), self.create_order("order-2")]

        def dispatch(order: dict, key: str) -> str:
            try:
                self.service.dispatch_order(order["id"], driver["id"], key)
                return "assigned"
            except DomainError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda item: dispatch(*item),
                    [(orders[0], "dispatch-1"), (orders[1], "dispatch-2")],
                )
            )
        self.assertCountEqual(results, ["assigned", "driver_unavailable"])
        self.assertEqual(
            sum(self.service.get_order(order["id"])["status"] == "assigned" for order in orders),
            1,
        )

    def test_dispatch_retry_is_idempotent(self) -> None:
        driver = self.create_driver()
        order = self.create_order()
        first = self.service.dispatch_order(order["id"], driver["id"], "dispatch-1")
        retry = self.service.dispatch_order(order["id"], driver["id"], "dispatch-1")
        self.assertEqual(first, retry)
        self.assertEqual(len(self.service.get_order(order["id"])["events"]), 2)

    def test_invalid_transition_does_not_mutate_order_or_event_history(self) -> None:
        order = self.create_order()
        with self.assertRaises(DomainError):
            self.service.transition_order(order["id"], "delivered", "deliver-early")
        current = self.service.get_order(order["id"])
        self.assertEqual(current["status"], "pending")
        self.assertEqual(len(current["events"]), 1)

    def test_cancelling_assigned_order_releases_driver(self) -> None:
        driver = self.create_driver()
        order = self.create_order()
        self.service.dispatch_order(order["id"], driver["id"], "dispatch-1")

        cancelled = self.service.transition_order(order["id"], "cancelled", "cancel-1")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(self.service.list_drivers()[0]["status"], "available")

    def test_state_survives_service_restart(self) -> None:
        order = self.create_order()
        restarted_service = FleetService(self.database_path)
        loaded = restarted_service.get_order(order["id"])
        self.assertEqual(loaded["status"], "pending")
        self.assertEqual(loaded["id"], order["id"])


if __name__ == "__main__":
    unittest.main()
