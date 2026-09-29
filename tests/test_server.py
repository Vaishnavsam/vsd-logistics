from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from fleet_ops.auth import AuthManager
from fleet_ops.server import FleetRequestHandler
from fleet_ops.service import FleetService


class FleetServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        FleetRequestHandler.service = FleetService(Path(self.temp_dir.name) / "fleet.sqlite3")
        FleetRequestHandler.auth = AuthManager("test-password-12345")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FleetRequestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        status, response = self.request(
            "POST", "/auth/login", {"password": "test-password-12345"}, with_auth=False
        )
        self.assertEqual(status, 200)
        self.access_token = response["access_token"]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp_dir.cleanup()

    def request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        key: str | None = None,
        with_auth: bool = True,
        driver_token: str | None = None,
        customer_token: str | None = None,
    ) -> tuple[int, dict | str]:
        headers = {}
        encoded_body = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            encoded_body = json.dumps(body).encode("utf-8")
        if key is not None:
            headers["Idempotency-Key"] = key
        if driver_token is not None:
            headers["X-Driver-Token"] = driver_token
        if customer_token is not None:
            headers["X-Customer-Tracking-Token"] = customer_token
        if with_auth and hasattr(self, "access_token"):
            headers["Authorization"] = f"Bearer {self.access_token}"
        request = urllib.request.Request(
            self.base_url + path, data=encoded_body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request) as response:
                payload = response.read()
                if response.headers.get_content_type() == "application/json":
                    return response.status, json.loads(payload)
                return response.status, payload.decode("utf-8")
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_http_flow_creates_order_dispatches_and_reads_event_history(self) -> None:
        _, driver = self.request(
            "POST", "/drivers", {"name": "Avery"}, key="driver-create"
        )
        _, order = self.request(
            "POST",
            "/orders",
            {"pickup": "Warehouse", "dropoff": "Customer"},
            key="order-create",
        )
        status, assigned = self.request(
            "POST",
            f"/orders/{order['id']}/dispatch",
            {"driver_id": driver["id"]},
            key="order-dispatch",
        )
        self.assertEqual(status, 200)
        self.assertEqual(assigned["status"], "assigned")

        status, detail = self.request("GET", f"/orders/{order['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(len(detail["events"]), 2)

    def test_write_without_idempotency_key_is_rejected(self) -> None:
        status, response = self.request(
            "POST", "/orders", {"pickup": "Warehouse", "dropoff": "Customer"}
        )
        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], "idempotency_key_required")

    def test_dashboard_and_allowlisted_assets_are_served(self) -> None:
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("VSD Logistics", page)
        self.assertIn("customer-tracking-dialog", page)
        self.assertIn('href="/product"', page)
        self.assertIn('class="preference-control theme-toggle"', page)
        self.assertIn('class="preference-control density-toggle"', page)
        self.assertIn('id="login-open"', page)
        self.assertIn('id="login-dialog"', page)
        self.assertNotIn('<dialog class="form-dialog login-dialog" id="login-dialog" open', page)
        self.assertIn('id="preview-banner"', page)
        self.assertIn('data-theme="dark"', page)

        status, script = self.request("GET", "/app.js")
        self.assertEqual(status, 200)
        self.assertIn("Create customer link", script)
        self.assertIn("Idempotency-Key", script)
        self.assertIn("Direct line", script)
        self.assertNotIn("/map/geocode", script)
        self.assertNotIn("/map/reverse", script)
        self.assertNotIn("/map/route", script)
        self.assertIn("vsd-dashboard-theme", script)
        self.assertIn("vsd-dashboard-density", script)
        self.assertIn("setDashboardTheme", script)
        self.assertIn("setDashboardDensity", script)
        self.assertIn("setOperatorAccess(false)", script)
        status, dashboard_styles = self.request("GET", "/styles.css")
        self.assertEqual(status, 200)
        self.assertIn(':root[data-theme="light"]', dashboard_styles)
        self.assertIn(':root[data-density="compact"]', dashboard_styles)

        status, product_page = self.request("GET", "/product", with_auth=False)
        self.assertEqual(status, 200)
        for section in ('id="solutions"', 'id="scope"', 'id="project"', 'id="faqs"'):
            self.assertIn(section, product_page)
        self.assertIn("THE VSD CLEARLOOP", product_page)
        self.assertIn("Three views. Clearer handoffs.", product_page)
        self.assertIn('d="M102 253 L481 94"', product_page)
        self.assertIn('id="walkthrough"', product_page)
        self.assertIn('id="walkthrough-play"', product_page)
        self.assertIn("SCHEMATIC, NOT A GEOGRAPHIC COVERAGE MAP", product_page)
        self.assertNotIn("—", product_page)
        self.assertIn("No address lookup, road routing, traffic prediction", product_page)
        status, product_styles = self.request("GET", "/product.css", with_auth=False)
        self.assertEqual(status, 200)
        self.assertIn(".product-hero", product_styles)
        self.assertIn(".workflow-film", product_styles)
        self.assertIn(".network-diagram", product_styles)
        self.assertIn("prefers-reduced-motion", product_styles)
        self.assertNotIn('content: "—"', product_styles)
        status, product_script = self.request("GET", "/product.js", with_auth=False)
        self.assertEqual(status, 200)
        self.assertIn("data-persona", product_page)
        self.assertIn("activePersona", product_script)
        self.assertIn("walkthroughSteps", product_script)
        self.assertIn("What makes VSD Logistics different?", product_page)
        self.assertIn("We won’t claim that no other logistics product", product_page)

        status, driver_page = self.request("GET", "/driver")
        self.assertEqual(status, 200)
        self.assertIn("Share your trip location", driver_page)

        status, driver_script = self.request("GET", "/driver.js")
        self.assertEqual(status, 200)
        self.assertIn("X-Driver-Token", driver_script)

        status, customer_page = self.request("GET", "/track", with_auth=False)
        self.assertEqual(status, 200)
        self.assertIn("Your shipment", customer_page)
        self.assertIn('name="referrer" content="strict-origin-when-cross-origin"', customer_page)
        status, customer_script = self.request("GET", "/customer.js", with_auth=False)
        self.assertEqual(status, 200)
        self.assertIn("/customer/tracking", customer_script)
        status, customer_styles = self.request("GET", "/customer.css", with_auth=False)
        self.assertEqual(status, 200)
        self.assertIn(".customer-map", customer_styles)

        status, response = self.request("GET", "/private.txt")
        self.assertEqual(status, 404)
        self.assertEqual(response["error"]["code"], "not_found")

    def test_customer_tracking_link_is_public_scoped_and_revocable(self) -> None:
        _, order = self.request(
            "POST",
            "/orders",
            {
                "pickup": "Warehouse",
                "dropoff": "Customer",
                "cargo_description": "Private cargo",
            },
            key="customer-order-create",
        )
        status, issued = self.request(
            "POST", f"/orders/{order['id']}/customer-tracking-token"
        )
        self.assertEqual(status, 201)
        status, public = self.request(
            "GET", "/customer/tracking", with_auth=False, customer_token=issued["token"]
        )
        self.assertEqual(status, 200)
        self.assertEqual(public["status"], "pending")
        self.assertEqual(public["pickup"], "Warehouse")
        self.assertNotIn("cargo_description", public)
        self.assertNotIn("Private cargo", str(public))
        self.assertNotIn("driver_id", public)

        status, denied = self.request(
            "GET", f"/orders/{order['id']}", with_auth=False
        )
        self.assertEqual(status, 401)
        self.assertEqual(denied["error"]["code"], "authentication_required")

        status, revoked = self.request(
            "DELETE", f"/orders/{order['id']}/customer-tracking-token"
        )
        self.assertEqual(status, 200)
        self.assertTrue(revoked["revoked"])
        status, invalid = self.request(
            "GET", "/customer/tracking", with_auth=False, customer_token=issued["token"]
        )
        self.assertEqual(status, 404)
        self.assertEqual(invalid["error"]["code"], "tracking_link_not_found")

    def test_protected_endpoints_require_authentication(self) -> None:
        status, response = self.request("GET", "/orders", with_auth=False)
        self.assertEqual(status, 401)
        self.assertEqual(response["error"]["code"], "authentication_required")

        status, response = self.request("GET", "/health", with_auth=False)
        self.assertEqual(status, 200)

    def test_map_provider_api_endpoints_are_not_exposed(self) -> None:
        for path in (
            "/map/geocode?q=Main+Street",
            "/map/reverse?latitude=40.7&longitude=-74",
            "/map/route?start_latitude=40.7&start_longitude=-74&end_latitude=40.8&end_longitude=-73.9",
        ):
            status, response = self.request("GET", path)
            self.assertEqual(status, 404)
            self.assertEqual(response["error"]["code"], "not_found")

    def test_login_rejects_bad_password_and_limits_repeated_attempts(self) -> None:
        for attempt in range(4):
            status, _ = self.request(
                "POST",
                "/auth/login",
                {"password": f"incorrect-{attempt}"},
                with_auth=False,
            )
            self.assertEqual(status, 401)
        status, response = self.request(
            "POST", "/auth/login", {"password": "incorrect-final"}, with_auth=False
        )
        self.assertEqual(status, 429)
        self.assertEqual(response["error"]["code"], "login_rate_limited")

    def test_logout_invalidates_access_token(self) -> None:
        status, _ = self.request("POST", "/auth/logout", with_auth=True)
        self.assertEqual(status, 200)
        status, response = self.request("GET", "/orders")
        self.assertEqual(status, 401)
        self.assertEqual(response["error"]["code"], "authentication_required")

    def test_cargo_and_vehicle_capacity_metadata_round_trips(self) -> None:
        driver_status, driver = self.request(
            "POST",
            "/drivers",
            {
                "name": "Avery",
                "vehicle": "Van 12",
                "max_weight_kg": 900,
                "max_volume_m3": 12,
            },
            key="capacity-driver",
        )
        order_status, order = self.request(
            "POST",
            "/orders",
            {
                "pickup": "Warehouse",
                "dropoff": "Customer",
                "cargo_description": "Boxed parts",
                "cargo_weight_kg": 250,
                "cargo_volume_m3": 4.5,
            },
            key="measured-order",
        )
        self.assertEqual(driver_status, 201)
        self.assertEqual(order_status, 201)
        self.assertEqual(driver["max_weight_kg"], 900)
        self.assertEqual(driver["vehicle"], "Van 12")
        self.assertEqual(order["cargo_description"], "Boxed parts")
        self.assertEqual(order["cargo_volume_m3"], 4.5)

    def test_http_dispatch_returns_conflict_for_overloaded_vehicle(self) -> None:
        _, driver = self.request(
            "POST",
            "/drivers",
            {"name": "Avery", "max_weight_kg": 100},
            key="limited-driver",
        )
        _, order = self.request(
            "POST",
            "/orders",
            {
                "pickup": "Warehouse",
                "dropoff": "Customer",
                "cargo_weight_kg": 101,
            },
            key="overweight-order",
        )
        status, response = self.request(
            "POST",
            f"/orders/{order['id']}/dispatch",
            {"driver_id": driver["id"]},
            key="overload-dispatch",
        )
        self.assertEqual(status, 409)
        self.assertEqual(response["error"]["code"], "driver_capacity_exceeded")

    def test_driver_token_reports_location_without_operator_session(self) -> None:
        _, driver = self.request(
            "POST", "/drivers", {"name": "Avery"}, key="gps-driver"
        )
        _, order = self.request(
            "POST",
            "/orders",
            {
                "pickup": "Warehouse",
                "dropoff": "Customer",
                "pickup_latitude": 40.7,
                "pickup_longitude": -74.0,
            },
            key="gps-order",
        )
        self.request(
            "POST",
            f"/orders/{order['id']}/dispatch",
            {"driver_id": driver["id"]},
            key="gps-dispatch",
        )
        self.request(
            "POST",
            f"/orders/{order['id']}/transitions",
            {"status": "in_transit"},
            key="gps-start",
        )
        status, issued = self.request(
            "POST", f"/drivers/{driver['id']}/tracking-token"
        )
        self.assertEqual(status, 201)
        self.assertTrue(issued["token"].startswith(f"{driver['id']}."))

        status, location = self.request(
            "POST",
            "/driver/location",
            {"latitude": 40.71, "longitude": -74.01, "accuracy_m": 9},
            with_auth=False,
            driver_token=issued["token"],
        )
        self.assertEqual(status, 201)
        self.assertEqual(location["latitude"], 40.71)

        status, detail = self.request("GET", f"/orders/{order['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["latest_location"]["longitude"], -74.01)
        self.assertEqual(len(detail["locations"]), 1)

        status, response = self.request(
            "GET", "/orders", with_auth=False, driver_token=issued["token"]
        )
        self.assertEqual(status, 401)
        self.assertEqual(response["error"]["code"], "authentication_required")

    def test_location_endpoint_rejects_missing_or_invalid_driver_token(self) -> None:
        status, response = self.request(
            "POST",
            "/driver/location",
            {"latitude": 40.71, "longitude": -74.01},
            with_auth=False,
        )
        self.assertEqual(status, 401)
        self.assertEqual(response["error"]["code"], "invalid_driver_token")

    def test_workflow_templates_can_be_applied_and_completed_over_http(self) -> None:
        status, created = self.request(
            "POST",
            "/workflows",
            {
                "name": "Retail drop-off",
                "description": "Proof-of-delivery steps",
                "steps": ["Confirm receiver", "Capture signature"],
            },
            key="http-workflow",
        )
        self.assertEqual(status, 201)
        _, listed = self.request("GET", "/workflows")
        self.assertEqual(listed["workflows"][0]["id"], created["id"])
        _, order = self.request(
            "POST",
            "/orders",
            {
                "pickup": "Depot",
                "dropoff": "Store",
                "workflow_id": created["id"],
            },
            key="http-workflow-order",
        )
        _, detail = self.request("GET", f"/orders/{order['id']}")
        self.assertEqual(detail["workflow_name"], "Retail drop-off")
        task_id = detail["workflow_tasks"][0]["id"]
        status, completed = self.request(
            "POST",
            f"/orders/{order['id']}/workflow-tasks/{task_id}/complete",
            {},
            key="http-workflow-task",
        )
        self.assertEqual(status, 200)
        self.assertEqual(completed["label"], "Confirm receiver")
        _, detail = self.request("GET", f"/orders/{order['id']}")
        self.assertIsNotNone(detail["workflow_tasks"][0]["completed_at"])
        status, updated = self.request(
            "POST",
            f"/workflows/{created['id']}/update",
            {
                "name": "Store handoff v2",
                "description": "Updated process",
                "steps": ["Confirm receiver"],
            },
            key="http-workflow-update",
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["name"], "Store handoff v2")
        status, deleted = self.request(
            "POST",
            f"/workflows/{created['id']}/delete",
            {},
            key="http-workflow-delete",
        )
        self.assertEqual(status, 200)
        self.assertTrue(deleted["deleted"])
        _, detail = self.request("GET", f"/orders/{order['id']}")
        self.assertEqual(detail["workflow_name"], "Retail drop-off")


if __name__ == "__main__":
    unittest.main()
