# Agent Playbook

## Project goal

Build a reliable, self-hostable logistics operations system for orders, drivers,
dispatch, and custom workflows. Payments, billing, finance, and external API
integrations are out of scope.

## Architecture

- Keep the first release as a modular monolith using Python's standard library.
- `src/fleet_ops/service.py` owns business rules and transaction boundaries.
- `src/fleet_ops/server.py` is an HTTP adapter; it must not implement business
  rules or access SQLite directly.
- `web/` is a dependency-free dark shipment-tracking frontend served by a fixed
  asset allowlist in the HTTP adapter; keep browser code presentation-focused
  and call service endpoints for all persisted behavior. The operator app
  includes overview, shipments, drivers, custom checklist workflows, and a
  separate read-only customer tracking page. The public `/product` page explains
  product value, user solutions, prototype scope, project steps, and FAQs. The
  map must not make geocoding
  or routing API calls. It may load attributed
  OpenStreetMap raster tiles directly; coordinates are operator-entered or
  manually adjusted, and the displayed line is straight-line only. Keep it
  distinct from authenticated driver GPS fixes and never imply road navigation
  or ETA.
- SQLite is the source of truth. Schema changes must be safe for existing
  databases, additive, and covered by a legacy-database test.
- Every accepted order mutation must be atomic and recorded in `order_events`.
- Business-command HTTP writes must require an `Idempotency-Key`; reusing a key
  with a different operation or payload must return a conflict. Driver location
  reports are append-only telemetry, and tracking-token issuance is an
  explicitly non-idempotent rotation that reveals the new secret only once.
- Operator APIs require a valid short-lived bearer session. The driver location
  endpoint uses a separate, rotatable, driver-scoped token and accepts updates
  only while that driver has a shipment in transit. Customer tracking uses a
  separate shipment-scoped token stored only as a salted hash, revealed once,
  and revocable by operators. Keep customer responses limited to shipment
  reference, pickup/drop-off labels, lifecycle status, and a recent in-transit
  GPS fix; never include cargo, driver identity, operator events, or workflow
  data. Never persist or log plaintext tracking tokens.
- Validate geographic coordinate bounds in the service. Rate-limit location
  reports, timestamp them on receipt, and retain no more than 30 days of GPS
  history. Never fabricate map positions or present planned route duration as a
  live traffic ETA.
- Keep the built-in HTTP server loopback-only; remote access requires HTTPS
  termination outside this development server.

## Order invariants

- An order has exactly one current state and one append-only event history.
- Only a pending order can be dispatched.
- A driver can be assigned to at most one active order.
- Dispatch assigns the order and driver in one database transaction.
- If cargo measurements and matching vehicle limits are recorded, dispatch
  rejects assignments that exceed either limit.
- Delivered and cancelled orders are terminal.
- A driver assigned to an order is released when that order is delivered or
  cancelled.
- GPS fixes are linked to a driver's current in-transit shipment, and the
  operator UI distinguishes fresh from stale fixes.
- Workflow templates are copied to a shipment at creation; later template
  changes must not rewrite that shipment's checklist. Task completion is
  audited and must not bypass the protected order lifecycle.
- Invalid commands must not partially update state or append success events.

## Agent workflow

1. Read this file and the relevant implementation and tests before editing.
2. Keep changes within the owning module; explain and test any cross-module
   change.
3. Add tests for normal behavior, invalid transitions, duplicate commands, and
   failure/rollback behavior where applicable.
4. Run `python -m unittest discover -s tests -v` from this directory.
5. Report the change, test command and result, and any remaining limitations.

## Style and safety

- Use type hints and clear domain-specific names.
- Prefer small functions and explicit validation; do not silently swallow
  exceptions.
- Never log request secrets or silently treat failed writes as success.
- Do not add payments, billing, finance, or outbound integrations.
- Do not add dependencies unless a concrete requirement justifies them.
- Keep the web app self-hosted and avoid third-party JavaScript runtime assets
  and fonts. Direct OpenStreetMap raster tile requests are the only map-related
  external requests; do not add geocoding or routing APIs without approval.
- Never display a live tracking state or vehicle location without a verified
  location update; show planned coordinate lines as straight-line only.
