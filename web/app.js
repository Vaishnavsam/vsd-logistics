const state = {
  orders: [],
  drivers: [],
  workflows: [],
  events: [],
  locations: [],
  page: "overview",
  editingWorkflowId: null,
  filter: "all",
  query: "",
  selectedOrderId: null,
  detailTab: "overview",
  toastTimer: null,
};

const byId = (id) => document.getElementById(id);
let accessToken = sessionStorage.getItem("vsd-access-token");
const mapViews = new Map();
let trackingRouteKey = "";
const makeKey = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
const initials = (name) => name.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase();
const labelStatus = (status) => status.replace("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
const shortId = (id) => id ? `#${id.slice(0, 8).toUpperCase()}` : "—";
const readError = (result, response) => result.error?.message ?? `Request failed (${response.status})`;

async function api(path, options = {}) {
  const { write, public: isPublic, ...requestOptions } = options;
  const headers = {
    ...(requestOptions.body ? { "Content-Type": "application/json" } : {}),
    ...(write ? { "Idempotency-Key": makeKey() } : {}),
    ...(!isPublic && accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
    ...requestOptions.headers,
  };
  const response = await fetch(path, {
    ...requestOptions,
    headers,
  });
  const result = await response.json();
  if (response.status === 401 && !isPublic && path !== "/auth/session") {
    accessToken = null;
    sessionStorage.removeItem("vsd-access-token");
    setOperatorAccess(false);
    openDialog("login-dialog");
  }
  if (!response.ok) throw new Error(readError(result, response));
  return result;
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function notify(message, isError = false) {
  const toast = byId("toast");
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.classList.add("visible");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => toast.classList.remove("visible"), 3200);
}

function readDisplayPreference(key, choices, fallback) {
  try {
    const preference = localStorage.getItem(key);
    return choices.includes(preference) ? preference : fallback;
  } catch (error) {
    console.warn(`Unable to read display preference "${key}".`, error);
    return fallback;
  }
}

function saveDisplayPreference(key, value) {
  try {
    localStorage.setItem(key, value);
    return true;
  } catch (error) {
    console.warn(`Unable to save display preference "${key}".`, error);
    return false;
  }
}

function setDashboardTheme(theme, persist = false) {
  const isDark = theme === "dark";
  document.documentElement.dataset.theme = isDark ? "dark" : "light";
  for (const button of document.querySelectorAll(".theme-toggle")) {
    button.textContent = isDark ? "☼ Light theme" : "☾ Dark theme";
    button.setAttribute("aria-label", `Switch to ${isDark ? "light" : "dark"} mode`);
    button.title = `Switch to ${isDark ? "light" : "dark"} mode`;
    button.setAttribute("aria-pressed", String(isDark));
  }
  if (persist && !saveDisplayPreference("vsd-dashboard-theme", theme)) {
    notify("Appearance changed for this session, but could not be saved in this browser.", true);
  }
}

function setDashboardDensity(density, persist = false) {
  const isCompact = density === "compact";
  document.documentElement.dataset.density = isCompact ? "compact" : "comfortable";
  for (const button of document.querySelectorAll(".density-toggle")) {
    button.textContent = isCompact ? "≡ Compact layout" : "▦ Comfortable layout";
    button.setAttribute("aria-label", `Switch to ${isCompact ? "comfortable" : "compact"} layout`);
    button.title = `Switch to ${isCompact ? "comfortable" : "compact"} layout`;
    button.setAttribute("aria-pressed", String(isCompact));
  }
  if (persist && !saveDisplayPreference("vsd-dashboard-density", density)) {
    notify("Layout changed for this session, but could not be saved in this browser.", true);
  }
}

function setOperatorAccess(signedIn) {
  byId("login-open").hidden = signedIn;
  byId("logout-button").hidden = !signedIn;
  byId("preview-banner").hidden = signedIn;
  if (!signedIn) byId("service-status").textContent = "Preview mode · sign in for live data";
}

setDashboardTheme(readDisplayPreference("vsd-dashboard-theme", ["dark", "light"], "dark"));
setDashboardDensity(readDisplayPreference("vsd-dashboard-density", ["comfortable", "compact"], "comfortable"));
setOperatorAccess(Boolean(accessToken));

function formatQuantity(value, unit) {
  return value == null ? "Not recorded" : `${Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 })} ${unit}`;
}

function formatAccuracy(value) {
  return value == null ? "accuracy not recorded" : `±${Math.round(value)} m`;
}

function statusClass(status) {
  return `status-${status ?? "pending"}`;
}

function filteredOrders() {
  const query = state.query.trim().toLowerCase();
  return state.orders.filter((order) => {
    const matchesFilter = state.filter === "all" || order.status === state.filter;
    const driver = state.drivers.find((candidate) => candidate.id === order.driver_id);
    const matchesQuery = !query || [
      order.id, order.pickup, order.dropoff, order.cargo_description, driver?.name, driver?.vehicle,
    ].some((value) => value?.toLowerCase().includes(query));
    return matchesFilter && matchesQuery;
  });
}

function setSelectedOrder(orderId) {
  state.selectedOrderId = orderId;
  state.filter = "all";
  document.querySelectorAll(".status-tab").forEach((tab) => {
    tab.classList.toggle("selected", tab.dataset.filter === "all");
  });
  navigateTo("shipments");
  const order = state.orders.find((candidate) => candidate.id === orderId);
  if (order) loadEvents(orderId);
  renderList();
  renderMap();
  renderDetails();
}

function navigateTo(page, preserveSearch = false) {
  state.page = page;
  const pages = ["overview", "shipments", "drivers", "workflows"];
  for (const name of pages) {
    const view = byId(`page-${name}`);
    if (view) view.hidden = name !== page;
    const nav = document.querySelector(`[data-page="${name}"]`);
    if (nav) nav.classList.toggle("active", name === page);
  }
  const titles = {
    overview: "Overview",
    shipments: "Shipment tracking",
    drivers: "Drivers",
    workflows: "Workflows",
  };
  byId("current-page-title").textContent = titles[page] ?? "Overview";
  byId("global-search").placeholder = page === "drivers"
    ? "Search drivers and vehicles…"
    : "Search shipments, locations, drivers…";
  if (!preserveSearch) {
    state.query = "";
    byId("global-search").value = "";
    byId("list-search").value = "";
    byId("driver-search").value = "";
  } else {
    byId("global-search").value = state.query;
  }
  renderOverviewPage();
  renderDriversPage();
  renderWorkflowsPage();
}

function renderOverviewPage() {
  byId("metric-active").textContent = state.orders.filter((order) => ["assigned", "in_transit"].includes(order.status)).length;
  byId("metric-pending").textContent = state.orders.filter((order) => order.status === "pending").length;
  const available = state.drivers.filter((driver) => driver.status === "available").length;
  byId("metric-drivers").textContent = available;
  byId("metric-fleet-copy").textContent = `of ${state.drivers.length} drivers ready`;
  byId("metric-delivered").textContent = state.orders.filter((order) => order.status === "delivered").length;

  const recent = byId("overview-shipments");
  recent.replaceChildren();
  const latestOrders = [...state.orders].sort((left, right) => new Date(right.created_at) - new Date(left.created_at)).slice(0, 6);
  if (!latestOrders.length) {
    recent.append(node("p", "empty-copy", "No shipments yet. Create one to start coordinating your operations."));
  }
  for (const order of latestOrders) {
    const driver = state.drivers.find((item) => item.id === order.driver_id);
    const row = node("button", "overview-shipment-row");
    row.type = "button";
    row.addEventListener("click", () => setSelectedOrder(order.id));
    const identity = node("span", "overview-shipment-identity");
    identity.append(node("strong", "", shortId(order.id)), node("small", "", `${order.pickup} → ${order.dropoff}`));
    row.append(identity, node("span", `status-pill ${statusClass(order.status)}`, labelStatus(order.status)));
    row.append(node("span", "overview-driver-name", driver?.name ?? "Unassigned"));
    recent.append(row);
  }
  const roster = byId("overview-drivers");
  roster.replaceChildren();
  const preview = state.drivers.slice(0, 5);
  if (!preview.length) roster.append(node("p", "empty-copy", "Add drivers to build your fleet roster."));
  for (const driver of preview) {
    const row = node("div", "fleet-summary-row");
    row.append(node("span", "driver-avatar", initials(driver.name)));
    const identity = node("span", "fleet-summary-identity");
    identity.append(node("strong", "", driver.name), node("small", "", driver.vehicle ?? "Vehicle not recorded"));
    row.append(identity, node("span", `status-pill ${driver.status === "available" ? "driver-available" : "driver-assigned"}`, labelStatus(driver.status)));
    roster.append(row);
  }
}

function renderDriversPage() {
  const query = (byId("driver-search")?.value ?? "").trim().toLowerCase();
  const visible = state.drivers.filter((driver) => `${driver.name} ${driver.vehicle ?? ""}`.toLowerCase().includes(query));
  const rows = byId("driver-rows");
  rows.replaceChildren();
  byId("driver-total").textContent = state.drivers.length;
  byId("driver-available").textContent = state.drivers.filter((driver) => driver.status === "available").length;
  byId("driver-assigned").textContent = state.drivers.filter((driver) => driver.status === "assigned").length;
  byId("driver-tracking").textContent = state.drivers.filter((driver) => driver.tracking_enabled).length;
  byId("drivers-empty").hidden = visible.length > 0;
  for (const driver of visible) {
    const row = node("tr");
    const name = node("td");
    const identity = node("span", "table-driver");
    identity.append(node("span", "driver-avatar", initials(driver.name)), node("strong", "", driver.name));
    name.append(identity);
    const vehicle = node("td", "", driver.vehicle ?? "Not recorded");
    const capacity = node("td", "", [
      driver.max_weight_kg == null ? "Weight —" : formatQuantity(driver.max_weight_kg, "kg"),
      driver.max_volume_m3 == null ? "Volume —" : formatQuantity(driver.max_volume_m3, "m³"),
    ].join(" · "));
    const status = node("td");
    status.append(node("span", `status-pill ${driver.status === "available" ? "driver-available" : "driver-assigned"}`, labelStatus(driver.status)));
    const tracking = node("td");
    tracking.append(node("span", `tracking-state${driver.tracking_enabled ? " enabled" : ""}`, driver.tracking_enabled ? "GPS token active" : "Not set up"));
    const actionCell = node("td", "driver-actions-cell");
    const action = node("button", "button button-subtle table-action", driver.tracking_enabled ? "Rotate token" : "Enable GPS");
    action.type = "button";
    action.addEventListener("click", () => issueDriverToken(driver.id, driver.tracking_enabled));
    actionCell.append(action);
    row.append(name, vehicle, capacity, status, tracking, actionCell);
    rows.append(row);
  }
}

function renderWorkflowsPage() {
  const list = byId("workflow-list");
  list.replaceChildren();
  byId("workflow-empty").hidden = state.workflows.length > 0;
  for (const workflow of state.workflows) {
    const card = node("article", "workflow-card");
    const header = node("div", "workflow-card-heading");
    header.append(node("span", "workflow-icon", "⟳"), node("span", "status-pill workflow-steps-pill", `${workflow.steps.length} steps`));
    card.append(header, node("h2", "", workflow.name));
    card.append(node("p", "workflow-description", workflow.description ?? "Reusable shipment checklist."));
    const steps = node("ol", "workflow-preview");
    workflow.steps.slice(0, 4).forEach((step) => steps.append(node("li", "", step)));
    if (workflow.steps.length > 4) steps.append(node("li", "more-steps", `+ ${workflow.steps.length - 4} more steps`));
    card.append(steps);
    const footer = node("div", "workflow-card-footer");
    footer.append(node("span", "", "Available for new shipments"));
    const actions = node("div", "workflow-card-actions");
    const edit = node("button", "text-action", "Edit");
    edit.type = "button";
    edit.addEventListener("click", () => editWorkflow(workflow));
    const remove = node("button", "text-action delete-workflow", "Delete");
    remove.type = "button";
    remove.addEventListener("click", () => deleteWorkflow(workflow));
    const use = node("button", "text-action", "Use workflow →");
    use.type = "button";
    use.addEventListener("click", () => {
      navigateTo("shipments");
      byId("order-workflow").value = workflow.id;
      openDialog("order-dialog");
    });
    actions.append(edit, remove, use);
    footer.append(actions);
    card.append(footer);
    list.append(card);
  }
}

function renderList() {
  const list = byId("shipment-list");
  list.replaceChildren();
  const orders = filteredOrders();
  byId("shipment-count").textContent = `${state.orders.length} shipment${state.orders.length === 1 ? "" : "s"}`;
  byId("filter-all").textContent = state.orders.length;
  byId("list-empty").hidden = orders.length > 0;
  byId("empty-title").textContent = state.orders.length ? "No matching shipments" : "No shipments yet";
  byId("empty-copy").textContent = state.orders.length
    ? "Try another search or status filter."
    : "Create a shipment to get your operations moving.";

  for (const order of orders) {
    const driver = state.drivers.find((candidate) => candidate.id === order.driver_id);
    const card = node("article", `shipment-card${order.id === state.selectedOrderId ? " selected" : ""}`);
    const selectCard = node("button", "shipment-card-select");
    selectCard.type = "button";
    selectCard.setAttribute("aria-pressed", String(order.id === state.selectedOrderId));
    selectCard.setAttribute("aria-label", `Show details for shipment ${shortId(order.id)}`);
    selectCard.addEventListener("click", () => setSelectedOrder(order.id));

    const top = node("div", "shipment-card-top");
    top.append(node("strong", "shipment-id", shortId(order.id)));
    top.append(node("span", `status-pill ${statusClass(order.status)}`, labelStatus(order.status)));
    selectCard.append(top);

    const progress = node("div", "card-route-track");
    progress.append(node("span", "route-code-small", "PICKUP"), node("span", "route-track-small"), node("span", "route-code-small", "DROP-OFF"));
    selectCard.append(progress);

    const route = node("div", "card-route");
    route.append(node("span", "card-pickup", order.pickup), node("span", "card-dropoff", order.dropoff));
    selectCard.append(route);

    const footer = node("div", "shipment-card-footer");
    const who = node("span", "card-driver");
    who.append(node("span", "tiny-avatar", driver ? initials(driver.name) : "—"), document.createTextNode(driver?.name ?? "Unassigned"));
    footer.append(who);
    footer.append(node("span", "cargo-preview", order.cargo_weight_kg == null ? "Cargo details pending" : formatQuantity(order.cargo_weight_kg, "kg")));
    selectCard.append(footer);
    card.append(selectCard);

    if (order.status === "pending") {
      const actions = node("div", "card-dispatch");
      const available = state.drivers.filter((candidate) => candidate.status === "available");
      if (available.length) {
        const select = node("select", "driver-select");
        select.setAttribute("aria-label", `Select driver for ${shortId(order.id)}`);
        for (const candidate of available) {
          const option = node("option", "", `${candidate.name}${candidate.vehicle ? ` · ${candidate.vehicle}` : ""}`);
          option.value = candidate.id;
          select.append(option);
        }
        const dispatch = node("button", "dispatch-label", "Dispatch →");
        dispatch.type = "button";
        actions.append(select, dispatch);
        actions.addEventListener("click", (event) => event.stopPropagation());
        select.addEventListener("change", (event) => { card.dataset.driverId = event.target.value; });
        card.dataset.driverId = select.value;
        dispatch.addEventListener("click", () => dispatchOrder(order, card.dataset.driverId));
      } else {
        actions.append(node("span", "no-driver-label", "Add an available driver to dispatch"));
      }
      card.append(actions);
    }

    list.append(card);
  }
}

function mapCoordinates(latitude, longitude, zoom) {
  const scale = 256 * 2 ** zoom;
  const lat = Math.max(-85.0511, Math.min(85.0511, latitude)) * Math.PI / 180;
  return {
    x: (longitude + 180) / 360 * scale,
    y: (1 - Math.asinh(Math.tan(lat)) / Math.PI) / 2 * scale,
  };
}

function mapLocation(latitude, longitude, view) {
  const center = mapCoordinates(view.center.latitude, view.center.longitude, view.zoom);
  const point = mapCoordinates(latitude, longitude, view.zoom);
  const scale = 256 * 2 ** view.zoom;
  let dx = point.x - center.x;
  if (dx > scale / 2) dx -= scale;
  if (dx < -scale / 2) dx += scale;
  return {
    x: view.width / 2 + dx,
    y: view.height / 2 + point.y - center.y,
  };
}

function fitMap(view, points) {
  if (!points.length || !view.width || !view.height) return;
  const average = points.reduce((sum, point) => ({
    latitude: sum.latitude + point.latitude / points.length,
    longitude: sum.longitude + point.longitude / points.length,
  }), { latitude: 0, longitude: 0 });
  view.center = average;
  for (let zoom = 19; zoom >= 2; zoom -= 1) {
    const projected = points.map((point) => mapCoordinates(point.latitude, point.longitude, zoom));
    const xs = projected.map((point) => point.x);
    const ys = projected.map((point) => point.y);
    if (Math.max(...xs) - Math.min(...xs) <= view.width * 0.65
      && Math.max(...ys) - Math.min(...ys) <= view.height * 0.6) {
      view.zoom = zoom;
      break;
    }
  }
}

function svgElement(name, attributes = {}) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
  return element;
}

function mapPoints(view) {
  return typeof view.getPoints === "function" ? view.getPoints() : [];
}

function drawMap(view, drawTiles = true) {
  const bounds = view.container.getBoundingClientRect();
  view.width = bounds.width;
  view.height = bounds.height;
  if (!view.width || !view.height) return;
  view.svg.setAttribute("viewBox", `0 0 ${view.width} ${view.height}`);
  if (drawTiles) {
    const center = mapCoordinates(view.center.latitude, view.center.longitude, view.zoom);
    const left = center.x - view.width / 2;
    const top = center.y - view.height / 2;
    const firstX = Math.floor(left / 256);
    const firstY = Math.floor(top / 256);
    const lastX = Math.floor((left + view.width) / 256);
    const lastY = Math.floor((top + view.height) / 256);
    const limit = 2 ** view.zoom;
    const fragment = document.createDocumentFragment();
    for (let tileY = firstY; tileY <= lastY; tileY += 1) {
      if (tileY < 0 || tileY >= limit) continue;
      for (let tileX = firstX; tileX <= lastX; tileX += 1) {
        const image = document.createElement("img");
        image.alt = "";
        image.draggable = false;
        image.addEventListener("error", () => view.container.classList.add("tiles-unavailable"), { once: true });
        image.addEventListener("load", () => view.container.classList.remove("tiles-unavailable"), { once: true });
        image.src = `https://tile.openstreetmap.org/${view.zoom}/${((tileX % limit) + limit) % limit}/${tileY}.png`;
        image.style.left = `${tileX * 256 - left}px`;
        image.style.top = `${tileY * 256 - top}px`;
        fragment.append(image);
      }
    }
    view.tiles.replaceChildren(fragment);
  }
  view.svg.replaceChildren();
  const stops = mapPoints(view).filter((stop) => stop.kind === "pickup" || stop.kind === "dropoff");
  if (stops.length === 2) {
    const start = mapLocation(stops[0].latitude, stops[0].longitude, view);
    const end = mapLocation(stops[1].latitude, stops[1].longitude, view);
    view.svg.append(svgElement("path", {
      d: `M${start.x.toFixed(1)} ${start.y.toFixed(1)} L${end.x.toFixed(1)} ${end.y.toFixed(1)}`,
      class: "direct-route",
    }));
  }
  for (const stop of mapPoints(view)) {
    if (stop.latitude == null || stop.longitude == null) continue;
    const point = mapLocation(stop.latitude, stop.longitude, view);
    const marker = svgElement("g", {
      class: `map-marker ${stop.kind}${stop.draggable ? " draggable" : ""}`,
      transform: `translate(${point.x} ${point.y})`,
      ...(stop.draggable ? { tabindex: "0", role: "button", "aria-label": `Adjust ${stop.kind} pin with arrow keys` } : {}),
    });
    if (stop.kind.startsWith("driver")) {
      marker.append(svgElement("circle", { class: "marker-shadow", r: "13" }));
      marker.append(svgElement("circle", { class: "marker-dot", r: "6" }));
      marker.append(svgElement("rect", { class: "marker-label-bg", x: "12", y: "-10", width: "57", height: "19", rx: "4" }));
      const label = svgElement("text", { class: "marker-label", x: "19", y: "3" });
      label.textContent = "DRIVER";
      marker.append(label);
    } else {
      const pin = "M0 0C-3-4-13-15-13-21a13 13 0 1 1 26 0C13-15 3-4 0 0Z";
      marker.append(svgElement("path", { class: "marker-shadow", d: pin }));
      marker.append(svgElement("circle", { class: "marker-dot", cx: "0", cy: "-21", r: "6" }));
      const letter = svgElement("text", { x: "0", y: "-18", "text-anchor": "middle" });
      letter.textContent = stop.label;
      marker.append(letter);
      const badgeText = stop.kind === "pickup" ? "PICKUP" : "DROP-OFF";
      const badgeWidth = badgeText.length * 5.5 + 14;
      const badgeX = stop.kind === "pickup" ? 12 : -badgeWidth - 12;
      marker.append(svgElement("rect", {
        class: "marker-label-bg",
        x: String(badgeX),
        y: "-34",
        width: String(badgeWidth),
        height: "19",
        rx: "4",
      }));
      const badge = svgElement("text", { class: "marker-label", x: String(badgeX + 7), y: "-21" });
      badge.textContent = badgeText;
      marker.append(badge);
    }
    if (stop.draggable) {
      marker.addEventListener("pointerdown", (event) => {
        event.stopPropagation();
        event.preventDefault();
        view.container.setPointerCapture(event.pointerId);
        const move = (moveEvent) => {
          const rect = view.container.getBoundingClientRect();
          const currentCenter = mapCoordinates(view.center.latitude, view.center.longitude, view.zoom);
          const worldX = currentCenter.x + moveEvent.clientX - rect.left - view.width / 2;
          const worldY = currentCenter.y + moveEvent.clientY - rect.top - view.height / 2;
          const scale = 256 * 2 ** view.zoom;
          const longitude = worldX / scale * 360 - 180;
          const latitude = Math.atan(Math.sinh(Math.PI * (1 - 2 * worldY / scale))) * 180 / Math.PI;
          stop.onMove(latitude, longitude);
          drawMap(view, false);
        };
        const finish = () => {
          view.container.removeEventListener("pointermove", move);
          view.container.removeEventListener("pointerup", finish);
          view.container.removeEventListener("pointercancel", finish);
        };
        view.container.addEventListener("pointermove", move);
        view.container.addEventListener("pointerup", finish, { once: true });
        view.container.addEventListener("pointercancel", finish, { once: true });
      });
      marker.addEventListener("keydown", (event) => {
        if (!["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(event.key)) return;
        event.preventDefault();
        const step = 0.001 * (event.shiftKey ? 10 : 1);
        const latitude = stop.latitude + (event.key === "ArrowUp" ? step : event.key === "ArrowDown" ? -step : 0);
        const longitude = stop.longitude + (event.key === "ArrowRight" ? step : event.key === "ArrowLeft" ? -step : 0);
        stop.onMove(latitude, longitude);
      });
    }
    view.svg.append(marker);
  }
}

function createMap(containerId, getPoints) {
  const container = byId(containerId);
  const view = {
    container,
    tiles: container.querySelector(".map-tiles"),
    svg: container.querySelector(".map-overlay"),
    center: { latitude: 0, longitude: 0 },
    zoom: 2,
    width: 0,
    height: 0,
    getPoints,
    needsFit: false,
  };
  mapViews.set(containerId, view);
  let panStart = null;
  container.addEventListener("pointerdown", (event) => {
    if (event.target.closest(".map-marker, .map-zoom-controls, a")) return;
    panStart = { x: event.clientX, y: event.clientY, center: { ...view.center } };
    container.setPointerCapture(event.pointerId);
    container.classList.add("panning");
  });
  container.addEventListener("pointermove", (event) => {
    if (!panStart) return;
    const start = mapCoordinates(panStart.center.latitude, panStart.center.longitude, view.zoom);
    const scale = 256 * 2 ** view.zoom;
    const worldX = start.x - (event.clientX - panStart.x);
    const worldY = start.y - (event.clientY - panStart.y);
    view.center = {
      latitude: Math.atan(Math.sinh(Math.PI * (1 - 2 * worldY / scale))) * 180 / Math.PI,
      longitude: worldX / scale * 360 - 180,
    };
    const offset = `translate(${event.clientX - panStart.x}px, ${event.clientY - panStart.y}px)`;
    view.tiles.style.transform = offset;
    view.svg.style.transform = offset;
  });
  const finishPan = () => {
    if (!panStart) return;
    panStart = null;
    container.classList.remove("panning");
    view.tiles.style.transform = "";
    view.svg.style.transform = "";
    drawMap(view);
  };
  container.addEventListener("pointerup", finishPan);
  container.addEventListener("pointercancel", finishPan);
  container.addEventListener("wheel", (event) => {
    event.preventDefault();
    setMapZoom(view, event.deltaY < 0 ? 1 : -1);
  }, { passive: false });
  new ResizeObserver(() => {
    const bounds = container.getBoundingClientRect();
    view.width = bounds.width;
    view.height = bounds.height;
    if (view.needsFit && view.width && view.height) {
      fitMap(view, mapPoints(view));
      view.needsFit = false;
    }
    drawMap(view);
  }).observe(container);
  return view;
}

function setMapZoom(view, amount) {
  view.zoom = Math.max(2, Math.min(19, view.zoom + amount));
  drawMap(view);
}

function mapRouteKey(start, end) {
  return `${start.latitude},${start.longitude};${end.latitude},${end.longitude}`;
}

function straightLineDistance(start, end) {
  const radians = Math.PI / 180;
  const latitudeDelta = (end.latitude - start.latitude) * radians;
  const longitudeDelta = (end.longitude - start.longitude) * radians;
  const haversine = Math.sin(latitudeDelta / 2) ** 2
    + Math.cos(start.latitude * radians) * Math.cos(end.latitude * radians)
    * Math.sin(longitudeDelta / 2) ** 2;
  return 6_371_000 * 2 * Math.atan2(Math.sqrt(haversine), Math.sqrt(1 - haversine));
}

function renderMap() {
  const order = state.orders.find((candidate) => candidate.id === state.selectedOrderId);
  byId("map-order-id").textContent = order ? shortId(order.id) : "No shipment selected";
  const status = byId("map-status");
  status.className = `status-pill ${statusClass(order?.status)}`;
  status.textContent = order ? labelStatus(order.status) : "—";
  const location = order?.latest_location;
  const fresh = location && Date.now() - new Date(location.recorded_at).getTime() < 120_000;
  byId("route-disclaimer").textContent = location
    ? `${fresh ? "GPS recent" : "Last GPS fix stale"} · direct line only`
    : "No driver GPS fix · direct line only";
  byId("gps-state").textContent = fresh ? "RECENT DRIVER FIX" : location ? "LAST FIX · STALE" : "GPS NOT CONNECTED";
  const start = order?.pickup_latitude != null && order?.pickup_longitude != null
    ? { latitude: Number(order.pickup_latitude), longitude: Number(order.pickup_longitude) } : null;
  const end = order?.dropoff_latitude != null && order?.dropoff_longitude != null
    ? { latitude: Number(order.dropoff_latitude), longitude: Number(order.dropoff_longitude) } : null;
  const view = mapViews.get("tracking-map");
  view.getPoints = () => [
    ...(start ? [{ ...start, label: "P", kind: "pickup" }] : []),
    ...(end ? [{ ...end, label: "D", kind: "dropoff" }] : []),
    ...(location ? [{ latitude: Number(location.latitude), longitude: Number(location.longitude), label: "Driver", kind: `driver${fresh ? "" : " stale"}` }] : []),
  ];
  byId("map-empty").textContent = order
    ? start || end || location
      ? "A direct line needs both pickup and drop-off coordinates."
      : "No map coordinates are saved for this shipment."
    : "Select a shipment with saved coordinates to plot a direct line.";
  byId("map-empty").hidden = Boolean(start || end || location);
  if (start && end) {
    const key = mapRouteKey(start, end);
    if (trackingRouteKey !== key) {
      trackingRouteKey = key;
      view.needsFit = true;
      if (view.width && view.height) {
        fitMap(view, [...view.getPoints()]);
        view.needsFit = false;
      }
    }
    const distance = (straightLineDistance(start, end) / 1000).toFixed(1);
    byId("map-route-summary").textContent = `Direct line · ${distance} km · no road routing`;
    byId("map-legend").textContent = location
      ? `Direct line · driver fix ${fresh ? "recent" : "stale"}`
      : "Direct pickup-to-drop-off line";
  } else {
    trackingRouteKey = "";
    byId("map-route-summary").textContent = start || end
      ? "A direct line needs both pickup and drop-off coordinates."
      : "This shipment has no saved coordinates.";
    const points = view.getPoints();
    if (points.length) fitMap(view, points);
    drawMap(view);
  }
}

function updateEditorCoordinates(kind, latitude, longitude) {
  byId(`${kind}-latitude`).value = latitude.toFixed(6);
  byId(`${kind}-longitude`).value = longitude.toFixed(6);
  const view = mapViews.get("order-map");
  drawMap(view, false);
  scheduleEditorRoute(false);
}

function editorPoints() {
  return ["pickup", "dropoff"].flatMap((kind) => {
    const latitudeValue = byId(`${kind}-latitude`).value.trim();
    const longitudeValue = byId(`${kind}-longitude`).value.trim();
    if (!latitudeValue || !longitudeValue) return [];
    const latitude = Number(latitudeValue);
    const longitude = Number(longitudeValue);
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return [];
    return [{
      latitude,
      longitude,
      label: kind === "pickup" ? "P" : "D",
      kind,
      draggable: true,
      onMove: (nextLatitude, nextLongitude) => updateEditorCoordinates(kind, nextLatitude, nextLongitude),
    }];
  });
}

function editorKey() {
  const [start, end] = ["pickup", "dropoff"].map((kind) => ({
    latitude: byId(`${kind}-latitude`).value.trim() ? Number(byId(`${kind}-latitude`).value) : NaN,
    longitude: byId(`${kind}-longitude`).value.trim() ? Number(byId(`${kind}-longitude`).value) : NaN,
  }));
  return [start, end].every((point) => Number.isFinite(point.latitude) && Number.isFinite(point.longitude))
    ? mapRouteKey(start, end) : "";
}

function scheduleEditorRoute(shouldFit = true) {
  const view = mapViews.get("order-map");
  view.getPoints = editorPoints;
  const key = editorKey();
  byId("order-map-empty").hidden = Boolean(key);
  if (!key) {
    drawMap(view, false);
    byId("order-route-status").textContent = "Enter both locations' coordinates to draw a direct line. No address lookup or road routing is used.";
    return;
  }
  const [start, end] = ["pickup", "dropoff"].map((kind) => ({
    latitude: Number(byId(`${kind}-latitude`).value),
    longitude: Number(byId(`${kind}-longitude`).value),
  }));
  if (shouldFit) fitMap(view, [start, end]);
  drawMap(view, false);
  const distance = (straightLineDistance(start, end) / 1000).toFixed(1);
  byId("order-route-status").textContent = `Direct line · ${distance} km · not a road route · no ETA`;
}

function renderProgress(status) {
  const levels = { pending: 0, assigned: 1, in_transit: 2, delivered: 4, cancelled: 0 };
  const level = levels[status] ?? 0;
  byId("progress-fill").style.width = `${Math.max(0, level / 4 * 100)}%`;
  byId("progress-fill").classList.toggle("cancelled", status === "cancelled");
  byId("route-progress-label").textContent = status === "cancelled"
    ? "Shipment cancelled"
    : status === "delivered"
      ? "Delivery complete"
      : status === "in_transit"
        ? "Shipment in transit · live position unavailable"
        : status === "assigned"
          ? "Driver assigned · awaiting pickup"
          : "Awaiting driver assignment";
  const location = state.orders.find((order) => order.id === state.selectedOrderId)?.latest_location;
  byId("route-progress-label").textContent = status === "in_transit" && location
    ? `Shipment in transit · position updated ${new Date(location.recorded_at).toLocaleTimeString()}`
    : byId("route-progress-label").textContent;
  byId("route-driver-label").textContent = status === "pending"
    ? "Driver unassigned"
    : location
      ? `${formatAccuracy(location.accuracy_m)} · ${new Date(location.recorded_at).toLocaleTimeString()}`
      : "GPS not connected";
}

function renderOverview(order, driver) {
  byId("detail-order-id").textContent = order ? shortId(order.id) : "Select a shipment";
  byId("detail-subtitle").textContent = order
    ? `${order.cargo_description ?? "General cargo"} · Created ${new Date(order.created_at).toLocaleString()}`
    : "Shipment and route details";
  byId("detail-pickup").textContent = order?.pickup ?? "Pickup location";
  byId("detail-dropoff").textContent = order?.dropoff ?? "Drop-off location";
  byId("pickup-code").textContent = order ? "ORIGIN" : "PICKUP";
  byId("dropoff-code").textContent = order ? "DESTINATION" : "DROP-OFF";
  const status = byId("detail-status");
  status.className = `status-pill ${statusClass(order?.status)}`;
  status.textContent = order ? labelStatus(order.status) : "—";

  byId("detail-driver-avatar").textContent = driver ? initials(driver.name) : "—";
  byId("detail-driver").textContent = driver?.name ?? "Unassigned";
  byId("detail-vehicle").textContent = driver
    ? driver.vehicle ?? "Vehicle not recorded"
    : "No driver assigned";
  byId("detail-driver-state").textContent = driver ? labelStatus(driver.status) : "—";
  byId("detail-cargo").textContent = order?.cargo_description ?? "General cargo";

  const weight = order?.cargo_weight_kg ?? null;
  const maxWeight = driver?.max_weight_kg ?? null;
  const volume = order?.cargo_volume_m3 ?? null;
  const maxVolume = driver?.max_volume_m3 ?? null;
  byId("detail-weight").textContent = weight === null
    ? "Not recorded"
    : maxWeight === null
      ? `${formatQuantity(weight, "kg")} · vehicle limit not recorded`
      : `${formatQuantity(weight, "kg")} / ${formatQuantity(maxWeight, "kg")}`;
  byId("detail-volume").textContent = volume === null
    ? "Not recorded"
    : maxVolume === null
      ? `${formatQuantity(volume, "m³")} · vehicle limit not recorded`
      : `${formatQuantity(volume, "m³")} / ${formatQuantity(maxVolume, "m³")}`;
  const weightRatio = weight !== null && maxWeight !== null ? weight / maxWeight : 0;
  byId("detail-weight-bar").style.width = `${Math.min(100, weightRatio * 100)}%`;
  byId("detail-weight-bar").classList.toggle("over-capacity", weight !== null && maxWeight !== null && weight > maxWeight);

  const deliveryTitle = {
    pending: "Awaiting dispatch",
    assigned: "Driver assigned",
    in_transit: "Shipment in transit",
    delivered: "Delivered",
    cancelled: "Cancelled",
  };
  byId("delivery-status-title").textContent = order ? deliveryTitle[order.status] : "Awaiting shipment";
  const location = order?.latest_location;
  byId("delivery-status-note").textContent = location
    ? `GPS update received ${new Date(location.recorded_at).toLocaleString()} · ${formatAccuracy(location.accuracy_m)}. ETA unavailable.`
    : "ETA unavailable without live location data.";
  byId("delivery-status-dot").className = `status-dot ${statusClass(order?.status)}`;
  renderProgress(order?.status);
}

function renderActivity(order) {
  const view = byId("view-activity");
  view.replaceChildren();
  byId("event-count").textContent = state.events.length;
  if (!order) {
    view.append(node("p", "activity-empty", "Select a shipment to view its activity."));
    return;
  }
  const heading = node("div", "tab-view-heading");
  heading.append(node("span", "section-kicker", "SHIPMENT HISTORY"), node("h3", "", "Order activity"));
  view.append(heading);
  const entries = [
    ...state.events.map((event) => ({ ...event, kind: "event" })),
    ...state.locations.map((location) => ({ ...location, kind: "location" })),
  ].sort((left, right) => new Date(left.created_at ?? left.recorded_at) - new Date(right.created_at ?? right.recorded_at));
  byId("event-count").textContent = entries.length;
  if (!entries.length) {
    view.append(node("p", "activity-empty", "No activity has been recorded."));
    return;
  }
  const timeline = node("div", "timeline");
  for (const event of entries) {
    const row = node("div", "timeline-entry");
    row.append(node("span", `timeline-dot${event.kind === "location" ? " location-dot" : ""}`));
    const detail = node("div", "timeline-detail");
    detail.append(node("strong", "", event.kind === "location" ? "Driver GPS update" : labelStatus(event.to_status)));
    detail.append(node("span", "", event.kind === "location"
      ? `${Number(event.latitude).toFixed(5)}, ${Number(event.longitude).toFixed(5)} · ${formatAccuracy(event.accuracy_m)}`
      : event.event_type.replaceAll("_", " ")));
    const time = node("time", "", new Date(event.created_at ?? event.recorded_at).toLocaleString());
    detail.append(time);
    row.append(detail);
    timeline.append(row);
  }
  view.append(timeline);
}

function renderCargo(order, driver) {
  const view = byId("view-cargo");
  view.replaceChildren();
  if (!order) {
    view.append(node("p", "activity-empty", "Select a shipment to view cargo details."));
    return;
  }
  const heading = node("div", "tab-view-heading");
  heading.append(node("span", "section-kicker", "LOAD DETAILS"), node("h3", "", order.cargo_description ?? "General cargo"));
  view.append(heading);
  const grid = node("div", "cargo-detail-grid");
  const weight = node("article", "cargo-detail-card");
  weight.append(node("span", "card-kicker", "CARGO WEIGHT"));
  weight.append(node("strong", "", formatQuantity(order.cargo_weight_kg, "kg")));
  weight.append(node("small", "", driver?.max_weight_kg == null ? "Vehicle weight capacity not recorded" : `Vehicle capacity: ${formatQuantity(driver.max_weight_kg, "kg")}`));
  const volume = node("article", "cargo-detail-card");
  volume.append(node("span", "card-kicker", "CARGO VOLUME"));
  volume.append(node("strong", "", formatQuantity(order.cargo_volume_m3, "m³")));
  volume.append(node("small", "", driver?.max_volume_m3 == null ? "Vehicle volume capacity not recorded" : `Vehicle capacity: ${formatQuantity(driver.max_volume_m3, "m³")}`));
  const assigned = node("article", "cargo-detail-card");
  assigned.append(node("span", "card-kicker", "ASSIGNED VEHICLE"));
  assigned.append(node("strong", "", driver?.vehicle ?? "Vehicle not recorded"));
  assigned.append(node("small", "", driver?.name ?? "No driver assigned"));
  grid.append(weight, volume, assigned);
  view.append(grid);
}

function renderWorkflowTasks(order) {
  const view = byId("view-workflow");
  view.replaceChildren();
  if (!order) {
    view.append(node("p", "activity-empty", "Select a shipment to view its workflow."));
    return;
  }
  const tasks = order.workflow_tasks ?? [];
  const completed = tasks.filter((task) => task.completed_at).length;
  byId("workflow-progress").textContent = `${completed}/${tasks.length}`;
  const heading = node("div", "tab-view-heading");
  heading.append(
    node("span", "section-kicker", "SHIPMENT CHECKLIST"),
    node("h3", "", order.workflow_name ?? "No workflow assigned"),
  );
  view.append(heading);
  if (!tasks.length) {
    view.append(node("p", "activity-empty", "This shipment has no custom workflow. Attach a checklist when creating a shipment."));
    return;
  }
  const progress = node("div", "workflow-task-progress");
  const progressText = node("span", "", `${completed} of ${tasks.length} tasks complete`);
  const track = node("span", "workflow-task-track");
  const progressBar = node("i");
  progressBar.style.width = `${completed / tasks.length * 100}%`;
  track.append(progressBar);
  progress.append(progressText, track);
  view.append(progress);
  const list = node("ol", "workflow-task-list");
  for (const task of tasks) {
    const item = node("li", `workflow-task${task.completed_at ? " completed" : ""}`);
    const label = node("span", "workflow-task-label");
    label.append(node("span", "workflow-check", task.completed_at ? "✓" : ""), node("strong", "", task.label));
    item.append(label);
    if (task.completed_at) {
      item.append(node("time", "", new Date(task.completed_at).toLocaleString()));
    } else {
      const complete = node("button", "button button-subtle table-action", "Mark complete");
      complete.type = "button";
      complete.addEventListener("click", () => completeWorkflowTask(order.id, task.id));
      item.append(complete);
    }
    list.append(item);
  }
  view.append(list);
}

function renderActions(order) {
  const actions = byId("detail-actions");
  actions.replaceChildren();
  if (!order) return;
  const customerLink = node(
    "button",
    "button button-subtle",
    order.customer_tracking_enabled ? "Rotate customer link" : "Create customer link",
  );
  customerLink.type = "button";
  customerLink.addEventListener("click", () => issueCustomerTrackingLink(order));
  actions.append(customerLink);
  if (order.customer_tracking_enabled) {
    const revokeLink = node("button", "button button-subtle", "Revoke customer link");
    revokeLink.type = "button";
    revokeLink.addEventListener("click", () => revokeCustomerTrackingLink(order));
    actions.append(revokeLink);
  }
  if (order.status === "pending") {
    const drivers = state.drivers.filter((driver) => driver.status === "available");
    if (!drivers.length) {
      actions.append(node("span", "action-note", "Add an available driver to dispatch this shipment."));
    } else {
      const label = node("label", "dispatch-picker");
      label.append(node("span", "", "ASSIGN DRIVER"));
      const select = node("select", "");
      select.setAttribute("aria-label", "Assign driver");
      for (const driver of drivers) {
        const option = node("option", "", `${driver.name}${driver.vehicle ? ` · ${driver.vehicle}` : ""}`);
        option.value = driver.id;
        select.append(option);
      }
      label.append(select);
      const dispatch = node("button", "button button-accent", "Dispatch shipment");
      dispatch.type = "button";
      dispatch.addEventListener("click", () => dispatchOrder(order, select.value));
      actions.append(label, dispatch);
    }
  } else if (order.status === "assigned" || order.status === "in_transit") {
    const next = order.status === "assigned" ? "in_transit" : "delivered";
    const action = node("button", "button button-accent", order.status === "assigned" ? "Mark in transit" : "Mark delivered");
    action.type = "button";
    action.addEventListener("click", () => transitionOrder(order, next));
    actions.append(action);
  }
  if (order.driver_id && ["assigned", "in_transit"].includes(order.status)) {
    const driver = state.drivers.find((candidate) => candidate.id === order.driver_id);
    const tokenButton = node(
      "button",
      "button button-subtle",
      driver?.tracking_enabled ? "Rotate GPS token" : "Set up driver GPS",
    );
    tokenButton.type = "button";
    tokenButton.addEventListener("click", () => issueDriverToken(order.driver_id, Boolean(driver?.tracking_enabled)));
    actions.append(tokenButton);
  }
  if (["pending", "assigned", "in_transit"].includes(order.status)) {
    const cancel = node("button", "button button-subtle", "Cancel shipment");
    cancel.type = "button";
    cancel.addEventListener("click", () => {
      if (window.confirm(`Cancel shipment ${shortId(order.id)}? This cannot be undone.`)) {
        transitionOrder(order, "cancelled");
      }
    });
    actions.append(cancel);
  }
}

function renderDetails() {
  const order = state.orders.find((candidate) => candidate.id === state.selectedOrderId);
  const driver = order?.driver_id
    ? state.drivers.find((candidate) => candidate.id === order.driver_id)
    : null;
  renderOverview(order, driver);
  renderActivity(order);
  renderCargo(order, driver);
  renderWorkflowTasks(order);
  renderActions(order);
  byId("view-overview").hidden = state.detailTab !== "overview";
  byId("view-activity").hidden = state.detailTab !== "activity";
  byId("view-cargo").hidden = state.detailTab !== "cargo";
  byId("view-workflow").hidden = state.detailTab !== "workflow";
}

async function loadEvents(orderId) {
  try {
    const result = await api(`/orders/${encodeURIComponent(orderId)}`);
    if (state.selectedOrderId !== orderId) return;
    state.events = result.events ?? [];
    state.locations = result.locations ?? [];
    renderDetails();
  } catch (error) {
    if (state.selectedOrderId === orderId) {
      state.events = [];
      state.locations = [];
      notify(error.message, true);
      renderDetails();
    }
  }
}

async function refresh() {
  try {
    const [orders, drivers, workflows] = await Promise.all([
      api("/orders"),
      api("/drivers"),
      api("/workflows"),
    ]);
    state.orders = orders.orders;
    state.drivers = drivers.drivers;
    state.workflows = workflows.workflows;
    const workflowSelect = byId("order-workflow");
    const selectedWorkflow = workflowSelect.value;
    workflowSelect.replaceChildren(new Option("No custom checklist", ""));
    for (const workflow of state.workflows) {
      workflowSelect.append(new Option(workflow.name, workflow.id));
    }
    if (state.workflows.some((workflow) => workflow.id === selectedWorkflow)) {
      workflowSelect.value = selectedWorkflow;
    }
    byId("service-status").textContent = "Local service connected";
    const selected = state.orders.some((order) => order.id === state.selectedOrderId);
    if (!selected) {
      const preferred = state.orders.find((order) => ["in_transit", "assigned", "pending"].includes(order.status)) ?? state.orders[0];
      state.selectedOrderId = preferred?.id ?? null;
    }
    renderList();
    renderMap();
    renderDetails();
    renderOverviewPage();
    renderDriversPage();
    renderWorkflowsPage();
    setOperatorAccess(true);
    if (state.selectedOrderId) loadEvents(state.selectedOrderId);
  } catch (error) {
    byId("service-status").textContent = "Service unavailable";
    notify(error.message, true);
  }
}

async function authenticateExistingSession() {
  if (!accessToken) {
    setOperatorAccess(false);
    return;
  }
  try {
    await api("/auth/session");
    await refresh();
  } catch {
    accessToken = null;
    sessionStorage.removeItem("vsd-access-token");
    setOperatorAccess(false);
  }
}

async function dispatchOrder(order, driverId) {
  try {
    await api(`/orders/${encodeURIComponent(order.id)}/dispatch`, {
      method: "POST", body: JSON.stringify({ driver_id: driverId }), write: true,
    });
    notify("Shipment dispatched.");
    await refresh();
  } catch (error) {
    notify(error.message, true);
    await refresh();
  }
}

async function transitionOrder(order, status) {
  try {
    await api(`/orders/${encodeURIComponent(order.id)}/transitions`, {
      method: "POST", body: JSON.stringify({ status }), write: true,
    });
    notify(status === "delivered"
      ? "Shipment delivered."
      : status === "cancelled"
        ? "Shipment cancelled."
        : "Shipment marked in transit.");
    await refresh();
  } catch (error) {
    notify(error.message, true);
    await refresh();
  }
}

async function completeWorkflowTask(orderId, taskId) {
  try {
    await api(
      `/orders/${encodeURIComponent(orderId)}/workflow-tasks/${encodeURIComponent(taskId)}/complete`,
      { method: "POST", body: JSON.stringify({}), write: true },
    );
    notify("Workflow step completed.");
    await refresh();
  } catch (error) {
    notify(error.message, true);
  }
}

async function issueDriverToken(driverId, rotate) {
  if (rotate && !window.confirm("Rotating this token immediately revokes the driver's current token. Continue?")) return;
  try {
    const result = await api(`/drivers/${encodeURIComponent(driverId)}/tracking-token`, { method: "POST" });
    byId("issued-driver-token").value = result.token;
    byId("issued-driver-token").type = "password";
    byId("toggle-token-visibility").textContent = "Show";
    await refresh();
    openDialog("tracking-token-dialog");
  } catch (error) {
    notify(error.message, true);
  }
}

async function issueCustomerTrackingLink(order) {
  if (order.customer_tracking_enabled
    && !window.confirm("Rotating this link immediately revokes the customer's current link. Continue?")) return;
  try {
    const result = await api(
      `/orders/${encodeURIComponent(order.id)}/customer-tracking-token`,
      { method: "POST" },
    );
    const link = new URL(`/track#${encodeURIComponent(result.token)}`, window.location.origin).href;
    byId("issued-customer-link").value = link;
    byId("open-customer-link").href = link;
    await refresh();
    openDialog("customer-tracking-dialog");
  } catch (error) {
    notify(error.message, true);
  }
}

async function revokeCustomerTrackingLink(order) {
  if (!window.confirm(`Revoke the customer tracking link for shipment ${shortId(order.id)}?`)) return;
  try {
    await api(
      `/orders/${encodeURIComponent(order.id)}/customer-tracking-token`,
      { method: "DELETE" },
    );
    notify("Customer tracking link revoked.");
    await refresh();
  } catch (error) {
    notify(error.message, true);
  }
}

function editWorkflow(workflow) {
  state.editingWorkflowId = workflow.id;
  byId("workflow-name").value = workflow.name;
  byId("workflow-description").value = workflow.description ?? "";
  byId("workflow-steps").value = workflow.steps.join("\n");
  byId("workflow-dialog-title").textContent = "Edit workflow";
  byId("workflow-submit").textContent = "Save changes";
  openDialog("workflow-dialog");
}

async function deleteWorkflow(workflow) {
  if (!window.confirm(`Delete the “${workflow.name}” template? Existing shipment checklists will be kept.`)) return;
  try {
    await api(`/workflows/${encodeURIComponent(workflow.id)}/delete`, {
      method: "POST",
      body: JSON.stringify({}),
      write: true,
    });
    await refresh();
    notify("Workflow template deleted. Existing shipment checklists were kept.");
  } catch (error) {
    notify(error.message, true);
  }
}

function openDialog(id) {
  const dialog = byId(id);
  if (!dialog.open) dialog.showModal();
}

function closeDialog(id) {
  const dialog = byId(id);
  if (dialog.open) dialog.close();
}

byId("order-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  byId("order-feedback").textContent = "";
  try {
    const order = await api("/orders", {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(new FormData(form))),
      write: true,
    });
    form.reset();
    scheduleEditorRoute();
    closeDialog("order-dialog");
    state.selectedOrderId = order.id;
    state.detailTab = "overview";
    await refresh();
    navigateTo("shipments");
    notify("New shipment created.");
  } catch (error) {
    byId("order-feedback").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

byId("driver-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  byId("driver-feedback").textContent = "";
  try {
    await api("/drivers", {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(new FormData(form))),
      write: true,
    });
    form.reset();
    closeDialog("driver-dialog");
    await refresh();
    notify("Driver added to the roster.");
  } catch (error) {
    byId("driver-feedback").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

byId("workflow-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const submit = form.querySelector('button[type="submit"]');
  const feedback = byId("workflow-feedback");
  const values = Object.fromEntries(new FormData(form));
  values.steps = String(values.steps_text)
    .split(/\r?\n/)
    .map((step) => step.trim())
    .filter(Boolean);
  delete values.steps_text;
  submit.disabled = true;
  feedback.textContent = "";
  try {
    const path = state.editingWorkflowId
      ? `/workflows/${encodeURIComponent(state.editingWorkflowId)}/update`
      : "/workflows";
    await api(path, {
      method: "POST",
      body: JSON.stringify(values),
      write: true,
    });
    form.reset();
    state.editingWorkflowId = null;
    byId("workflow-dialog-title").textContent = "Create a workflow";
    byId("workflow-submit").textContent = "Save workflow";
    closeDialog("workflow-dialog");
    await refresh();
    navigateTo("workflows");
    notify("Workflow saved for future shipments.");
  } catch (error) {
    feedback.textContent = error.message;
  } finally {
    submit.disabled = false;
  }
});

byId("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const submit = form.querySelector('button[type="submit"]');
  const feedback = byId("login-feedback");
  submit.disabled = true;
  feedback.textContent = "";
  try {
    const result = await api("/auth/login", {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(new FormData(form))),
      public: true,
    });

    accessToken = result.access_token;
    sessionStorage.setItem("vsd-access-token", accessToken);
    setOperatorAccess(true);
    form.reset();
    closeDialog("login-dialog");
    await refresh();
  } catch (error) {
    feedback.textContent = error.message;
  } finally {
    submit.disabled = false;
  }
});

byId("new-order-open").addEventListener("click", () => openDialog("order-dialog"));
document.querySelectorAll("[data-open-order]").forEach((button) => {
  button.addEventListener("click", () => openDialog("order-dialog"));
});
byId("empty-create").addEventListener("click", () => openDialog("order-dialog"));
byId("order-dialog").addEventListener("click", () => requestAnimationFrame(() => {
  const view = mapViews.get("order-map");
  if (view) drawMap(view);
}));
for (const kind of ["pickup", "dropoff"]) {
  byId(kind).addEventListener("input", () => {
    byId(`${kind}-latitude`).value = "";
    byId(`${kind}-longitude`).value = "";
    scheduleEditorRoute();
  });
}
for (const input of ["pickup-latitude", "pickup-longitude", "dropoff-latitude", "dropoff-longitude"]) {
  byId(input).addEventListener("input", scheduleEditorRoute);
}
document.querySelectorAll("[data-map-zoom]").forEach((button) => {
  button.addEventListener("click", () => {
    const view = mapViews.get("order-map");
    setMapZoom(view, button.dataset.mapZoom === "in" ? 1 : -1);
  });
});
byId("add-driver-open").addEventListener("click", () => openDialog("driver-dialog"));
document.querySelectorAll("[data-open-driver]").forEach((button) => {
  button.addEventListener("click", () => openDialog("driver-dialog"));
});
byId("new-workflow-open").addEventListener("click", () => openDialog("workflow-dialog"));
byId("first-workflow-open").addEventListener("click", () => openDialog("workflow-dialog"));
byId("workflow-dialog").addEventListener("close", () => {
  if (!state.editingWorkflowId) return;
  state.editingWorkflowId = null;
  byId("workflow-form").reset();
  byId("workflow-dialog-title").textContent = "Create a workflow";
  byId("workflow-submit").textContent = "Save workflow";
});
byId("refresh-button").addEventListener("click", refresh);
byId("list-refresh").addEventListener("click", refresh);
byId("login-open").addEventListener("click", () => openDialog("login-dialog"));
document.querySelectorAll("[data-login]").forEach((button) => {
  button.addEventListener("click", () => openDialog("login-dialog"));
});
document.querySelectorAll(".theme-toggle").forEach((button) => button.addEventListener("click", () => {
  const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  setDashboardTheme(theme, true);
  notify(`${theme === "dark" ? "Dark" : "Light"} appearance enabled.`);
}));
document.querySelectorAll(".density-toggle").forEach((button) => button.addEventListener("click", () => {
  const density = document.documentElement.dataset.density === "compact" ? "comfortable" : "compact";
  setDashboardDensity(density, true);
  notify(`${density === "compact" ? "Compact" : "Comfortable"} layout enabled.`);
}));
byId("logout-button").addEventListener("click", async () => {
  try {
    await api("/auth/logout", { method: "POST" });
  } catch {
    sessionStorage.removeItem("vsd-access-token");
    accessToken = null;
    openDialog("login-dialog");
  }
  sessionStorage.removeItem("vsd-access-token");
  accessToken = null;
  state.orders = [];
  state.drivers = [];
  state.workflows = [];
  state.events = [];
  state.locations = [];
  state.selectedOrderId = null;
  setOperatorAccess(false);
  renderList();
  renderMap();
  renderDetails();
  renderOverviewPage();
  renderDriversPage();
  renderWorkflowsPage();
  closeDialog("login-dialog");
  openDialog("login-dialog");
});
byId("global-search").addEventListener("input", (event) => {
  if (state.page === "drivers") {
    byId("driver-search").value = event.target.value;
    renderDriversPage();
  } else {
    state.query = event.target.value;
    byId("list-search").value = state.query;
    if (state.page !== "shipments") navigateTo("shipments", true);
    renderList();
  }
});
byId("list-search").addEventListener("input", (event) => {
  state.query = event.target.value;
  byId("global-search").value = state.query;
  renderList();
});
byId("driver-search").addEventListener("input", (event) => {
  byId("global-search").value = event.target.value;
  renderDriversPage();
});

document.querySelectorAll(".status-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    state.filter = tab.dataset.filter;
    document.querySelectorAll(".status-tab").forEach((item) => item.classList.toggle("selected", item === tab));
    renderList();
  });
});

document.querySelectorAll(".detail-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    state.detailTab = tab.dataset.tab;
    document.querySelectorAll(".detail-tab").forEach((item) => {
      const active = item === tab;
      item.classList.toggle("active", active);
      item.setAttribute("aria-selected", String(active));
    });
    renderDetails();
  });
});

document.querySelectorAll("[data-close]").forEach((button) => {
  button.addEventListener("click", () => closeDialog(button.dataset.close));
});

byId("tracking-token-dialog").addEventListener("close", () => {
  byId("issued-driver-token").value = "";
  byId("issued-driver-token").type = "password";
  byId("toggle-token-visibility").textContent = "Show";
});
byId("customer-tracking-dialog").addEventListener("close", () => {
  byId("issued-customer-link").value = "";
  byId("open-customer-link").removeAttribute("href");
});
byId("toggle-token-visibility").addEventListener("click", () => {
  const input = byId("issued-driver-token");
  input.type = input.type === "password" ? "text" : "password";
  byId("toggle-token-visibility").textContent = input.type === "password" ? "Show" : "Hide";
});
byId("copy-driver-token").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(byId("issued-driver-token").value);
    notify("Driver token copied. Store it securely.");
  } catch (error) {
    notify("Clipboard access failed. Reveal and copy the token manually.", true);
  }
});
byId("copy-customer-link").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(byId("issued-customer-link").value);
    notify("Customer tracking link copied. Share it securely.");
  } catch (error) {
    notify("Clipboard access failed. Copy the link manually.", true);
  }
});

document.querySelectorAll("[data-page]").forEach((button) => {
  button.addEventListener("click", () => navigateTo(button.dataset.page));
});

byId("map-zoom-in").addEventListener("click", () => setMapZoom(mapViews.get("tracking-map"), 1));
byId("map-zoom-out").addEventListener("click", () => setMapZoom(mapViews.get("tracking-map"), -1));
byId("map-reset").addEventListener("click", () => {
  const view = mapViews.get("tracking-map");
  const points = view.getPoints();
  if (points.length) fitMap(view, points);
  else { view.center = { latitude: 0, longitude: 0 }; view.zoom = 2; }
  drawMap(view);
});

document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    byId("global-search").focus();
  } else if (event.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) {
    event.preventDefault();
    byId("list-search").focus();
  } else if (event.key === "Escape") {
    closeDialog("order-dialog");
    closeDialog("driver-dialog");
  }
});

createMap("tracking-map", () => []);
createMap("order-map", editorPoints);
drawMap(mapViews.get("tracking-map"));
drawMap(mapViews.get("order-map"));
authenticateExistingSession();
setInterval(() => {
  if (accessToken) refresh();
}, 15_000);
