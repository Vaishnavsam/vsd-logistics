# VSD Logistics

A dependency-free starter for a self-hosted logistics operations platform. The
first vertical slice covers driver registration, order creation, atomic dispatch,
order status transitions, authenticated operator access, driver-scoped GPS
reporting, customer-scoped read-only tracking links, durable state, audit events,
and idempotent commands.

Payments, billing, and finance are excluded. The map uses only the address,
street-tile, and driving-route providers described below.

## Requirements

- Python 3.11 or newer

No third-party packages are required.

## Run

From this directory:

```powershell
$env:PYTHONPATH = "$PWD\src"
$env:FLEET_OPS_DB = "fleet-ops.sqlite3"
$env:FLEET_OPS_ADMIN_PASSWORD = "replace-this-with-a-unique-password"
$env:FLEET_OPS_HOST = "127.0.0.1"
$env:FLEET_OPS_PORT = "8080"
python -m fleet_ops.server
```

Set a unique password of at least 12 characters before starting; the service
refuses to start without it. The dashboard opens in preview without a password
dialog; signing in is required to load or change live operational data. Sessions
are bearer tokens held in the current browser tab and expire after eight hours;
restarting the service invalidates all sessions. Failed passwords are rate
limited per client. The built-in HTTP server only binds to loopback, so do not
expose it directly to a network. Remote access requires a properly configured
HTTPS reverse proxy.

The API listens on `http://127.0.0.1:8080`. State is stored in SQLite at the
path specified by `FLEET_OPS_DB` (default: `fleet-ops.sqlite3`).
Open `http://127.0.0.1:8080` for the web dashboard. It supports creating orders
and drivers, searching and filtering the shipment list, dispatching orders, and
advancing delivery status. Its dark tracking-console layout pairs a shipment list
with a route overview and shipment-detail tabs for order history and cargo
capacity and an operational checklist. The operator web app includes overview
metrics and recent activity, a searchable shipment tracker, a fleet roster with
driver-location token controls, customer link controls, and custom reusable
checklist workflows. Use the appearance and layout controls in the dashboard
header to switch between dark and light themes and compact or comfortable
spacing. These display preferences are saved in the current browser.
Customers use a separate, read-only `/track#...` page.
The public `/product` page introduces the operator, driver, and customer
experiences; documents the prototype's included and excluded scope; explains
the project path; and provides an interactive, role-based FAQ with topic filters
and search. It is informational and does not require an operator session. Product
positioning describes the prototype's actual workflow and boundaries without
making unverified exclusivity or competitor claims.
When creating a shipment, operators enter pickup/drop-off labels and can
optionally provide coordinates or adjust pins by dragging. The map draws a
straight line between saved coordinates and reports only their straight-line
distance; it does not geocode addresses, follow roads, or calculate an ETA.
Labels and coordinates are stored with the shipment. Existing records without
coordinates remain intact and do not get fabricated map positions. After dispatch, an operator can issue
a one-time driver token. The driver opens `/driver`, pastes the token, and
explicitly allows browser location sharing. Fixes are sent every 15 seconds
while the page is active, timestamped by the server, and accepted only when the
driver has a shipment in transit. Updates are limited to one every five seconds
per driver and GPS history is retained for up to 30 days. The tracking console
displays a dark-styled OpenStreetMap basemap, a direct line between saved
coordinates, and the driver's latest GPS fix when available. Driver locations
refresh every 15 seconds; a fix older than two minutes is marked stale. The
customer page shows only the shipment reference, pickup/drop-off labels,
customer-safe lifecycle updates, and a GPS fix no older than two minutes while
the shipment is in transit. It does not reveal cargo details, driver identity,
operator event metadata, or workflow tasks. Customer tracking links are
shipment-scoped, stored as salted hashes, shown only once, and can be rotated or
revoked from the operator shipment screen. The secret is kept in the link
fragment (not sent with the page request) and is submitted only to the dedicated
public tracking endpoint; page responses are non-cacheable and suppress
path-level referrers on cross-origin requests. Share links only with their
intended recipients.
The map makes no application API calls for geocoding or routing. It fetches map
image tiles directly from the public OpenStreetMap tile server, so map viewport
requests still reach that tile host; shipment labels and coordinates are not
sent there. The map remains usable for stored pins and direct lines if no tiles
load. Workflow templates contain up to 20 ordered checklist
steps. A shipment receives a snapshot of its selected template, and completion
of each task is recorded in its shipment history without changing lifecycle
state. The dashboard and API use the same local service and database. No third-party
JavaScript libraries or fonts are loaded.

## API walkthrough

Every write requires a unique `Idempotency-Key` header. Repeating the same
operation with the same key and payload returns the original response. Reusing
a key for a different operation or payload returns `409 Conflict`. The
one-time driver-token issuance/rotation and append-only GPS reporting endpoints
and customer-link issuance/rotation/revocation are intentional exceptions;
token rotation replaces the prior token.

Authenticate operator API requests:

```powershell
$operator = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/auth/login `
  -ContentType "application/json" `
  -Body '{"password":"your-configured-operator-password"}'
$operatorHeaders = @{ Authorization = "Bearer $($operator.access_token)" }
```

Create a driver:

```powershell
$driver = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/drivers `
  -Headers ($operatorHeaders + @{ "Idempotency-Key" = "driver-create-001" }) `
  -ContentType "application/json" `
  -Body '{"name":"Avery Driver","vehicle":"Van 12","max_weight_kg":1200,"max_volume_m3":12}'
```

Issue a driver-scoped token from the operator session. The plaintext is returned
only once; store and share it securely. Issuing another token revokes the prior
one:

```powershell
$tracking = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8080/drivers/$($driver.id)/tracking-token" `
  -Headers $operatorHeaders
$tracking.token
```

Create a customer link using the order ID. The token is returned only once;
issuing a replacement revokes the previous link. The customer page accepts only
this shipment's sanitized status and a recent in-transit location. Revoke access
with `DELETE /orders/{order_id}/customer-tracking-token`.

```powershell
$customerTracking = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8080/orders/ORDER_ID/customer-tracking-token" `
  -Headers $operatorHeaders
$customerLink = "http://127.0.0.1:8080/track#$($customerTracking.token)"
$customerLink
```

Drivers can open `http://127.0.0.1:8080/driver`, paste the token, and explicitly
grant location permission. For a direct test after the driver is in transit:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/driver/location `
  -Headers @{ "X-Driver-Token" = $tracking.token } `
  -ContentType "application/json" `
  -Body '{"latitude":40.7128,"longitude":-74.0060,"accuracy_m":18}'
```

Create an order:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/orders `
  -Headers ($operatorHeaders + @{ "Idempotency-Key" = "order-create-001" }) `
  -ContentType "application/json" `
  -Body '{"pickup":"Warehouse A","dropoff":"Customer B","cargo_description":"Boxed parts","cargo_weight_kg":350,"cargo_volume_m3":4.5,"pickup_latitude":40.7128,"pickup_longitude":-74.0060,"dropoff_latitude":40.7580,"dropoff_longitude":-73.9855}'
```

The cargo description, weight, and volume are optional. Vehicle payload and
volume limits are also optional. Dispatch rejects a measured cargo weight or
volume above the matching configured vehicle limit.

Dispatch the order using the returned order and driver IDs:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8080/orders/ORDER_ID/dispatch `
  -Headers ($operatorHeaders + @{ "Idempotency-Key" = "dispatch-001" }) `
  -ContentType "application/json" -Body '{"driver_id":"DRIVER_ID"}'
```

Advance its lifecycle:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8080/orders/ORDER_ID/transitions `
  -Headers ($operatorHeaders + @{ "Idempotency-Key" = "order-start-001" }) `
  -ContentType "application/json" -Body '{"status":"in_transit"}'

Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8080/orders/ORDER_ID/transitions `
  -Headers ($operatorHeaders + @{ "Idempotency-Key" = "order-deliver-001" }) `
  -ContentType "application/json" -Body '{"status":"delivered"}'
```

Read orders and drivers using `GET /orders` and `GET /drivers` with the operator
bearer token; get one order using `GET /orders/{order_id}`. Detailed order
responses include the GPS history recorded during that shipment's transit.
`GET /health` reports service availability.
The additive SQLite startup migration preserves existing orders and drivers
without cargo or vehicle metadata.

Create a reusable checklist workflow with `POST /workflows`, passing a `name`,
optional `description`, and a `steps` array of 1 to 20 task labels. `GET
/workflows` lists templates. Add `workflow_id` when creating an order to attach
a snapshot of that checklist. Complete a task with
`POST /orders/{order_id}/workflow-tasks/{task_id}/complete` and a unique
`Idempotency-Key`; completions appear in the order event history.

Valid lifecycle transitions:

```text
pending     -> assigned, cancelled
assigned    -> in_transit, cancelled
in_transit  -> delivered, cancelled
delivered   -> (terminal)
cancelled   -> (terminal)
```

Every state change is committed together with its corresponding append-only
event. The service uses SQLite transactions (`BEGIN IMMEDIATE`) so competing
dispatch requests cannot assign one driver to multiple active orders.

## Test

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m unittest discover -s tests -v
```

## Current boundaries

This starter is a single-process development service, not yet a production
deployment. Authentication is a single shared operator password, not a user or role
management system. Driver tokens are individually scoped to location reporting,
not full driver accounts or role-based access control. The built-in map is
uses public OpenStreetMap tiles and direct coordinate lines, not road navigation,
address verification, or a guaranteed ETA service. GPS retention policies beyond the default
30-day window, operator identity management, multi-instance deployment, and
advanced workflow rules such as branching, triggers, and approvals need
deliberate production requirements before deployment.

## Public preview

The GitHub Pages workflow publishes the interactive product overview and FAQs
as a static informational site. It does not host the Python service, SQLite
database, authenticated operator dashboard, driver GPS endpoint, or customer
tracking API. Live operations require running the self-hosted service described
above; do not publish its database or expose its loopback-only server directly.
