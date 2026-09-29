"use strict";

const byId = (id) => document.getElementById(id);
const statusLabels = {
  pending: "Shipment created",
  assigned: "Driver assigned",
  in_transit: "On the way",
  delivered: "Delivered",
  cancelled: "Cancelled",
};
const statusClasses = {
  pending: "status-pending",
  assigned: "status-assigned",
  in_transit: "status-in_transit",
  delivered: "status-delivered",
  cancelled: "status-cancelled",
};
const token = window.location.hash.slice(1);
let requestActive = false;
let mapRenderKey = null;

function formatDate(value) {
  if (!value) return "Time unavailable";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Time unavailable" : date.toLocaleString();
}

function project(latitude, longitude, zoom) {
  const size = 256 * 2 ** zoom;
  const clamped = Math.max(-85.0511, Math.min(85.0511, latitude));
  const sin = Math.sin(clamped * Math.PI / 180);
  return {
    x: (longitude + 180) / 360 * size,
    y: (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * size,
  };
}

function renderMap(shipment) {
  const points = [];
  if (shipment.pickup_latitude != null && shipment.pickup_longitude != null) {
    points.push({ kind: "pickup", latitude: shipment.pickup_latitude, longitude: shipment.pickup_longitude });
  }
  if (shipment.dropoff_latitude != null && shipment.dropoff_longitude != null) {
    points.push({ kind: "dropoff", latitude: shipment.dropoff_latitude, longitude: shipment.dropoff_longitude });
  }
  if (shipment.latest_location) {
    points.push({ kind: "live", ...shipment.latest_location });
  }
  const map = byId("customer-map");
  const empty = byId("customer-map-empty");
  const tiles = byId("customer-map-tiles");
  const overlay = byId("customer-map-overlay");
  const mapStatus = byId("tracking-location-status");
  mapStatus.textContent = shipment.latest_location
    ? `Live location · ${formatDate(shipment.latest_location.recorded_at)}`
    : shipment.status === "in_transit" ? "No recent driver location" : "No live location";
  const width = map.clientWidth;
  const height = map.clientHeight;
  const renderKey = `${width}x${height}:${points.map((point) => (
    `${point.kind}:${Number(point.latitude).toFixed(4)},${Number(point.longitude).toFixed(4)}`
  )).join("|")}`;
  if (renderKey === mapRenderKey) return;
  mapRenderKey = renderKey;
  empty.hidden = points.length > 0;
  tiles.replaceChildren();
  overlay.replaceChildren();
  if (!points.length) return;

  let zoom = 15;
  let projected = [];
  while (zoom > 2) {
    projected = points.map((point) => project(point.latitude, point.longitude, zoom));
    const xs = projected.map((point) => point.x);
    const ys = projected.map((point) => point.y);
    if (Math.max(...xs) - Math.min(...xs) <= width - 100
      && Math.max(...ys) - Math.min(...ys) <= height - 100) break;
    zoom -= 1;
  }
  projected = points.map((point) => project(point.latitude, point.longitude, zoom));
  const centerX = projected.reduce((sum, point) => sum + point.x, 0) / projected.length;
  const centerY = projected.reduce((sum, point) => sum + point.y, 0) / projected.length;
  const positions = projected.map((point) => ({
    x: point.x - centerX + width / 2,
    y: point.y - centerY + height / 2,
  }));

  const tileSize = 256;
  const firstX = Math.floor((centerX - width / 2) / tileSize);
  const lastX = Math.floor((centerX + width / 2) / tileSize);
  const firstY = Math.floor((centerY - height / 2) / tileSize);
  const lastY = Math.floor((centerY + height / 2) / tileSize);
  const worldTiles = 2 ** zoom;
  for (let x = firstX; x <= lastX; x += 1) {
    for (let y = firstY; y <= lastY; y += 1) {
      if (y < 0 || y >= worldTiles) continue;
      const image = document.createElement("img");
      image.alt = "";
      image.decoding = "async";
      image.referrerPolicy = "no-referrer";
      image.src = `https://tile.openstreetmap.org/${zoom}/${((x % worldTiles) + worldTiles) % worldTiles}/${y}.png`;
      image.style.left = `${x * tileSize - (centerX - width / 2)}px`;
      image.style.top = `${y * tileSize - (centerY - height / 2)}px`;
      tiles.append(image);
    }
  }
  overlay.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const pointByKind = new Map(points.map((point, index) => [point.kind, positions[index]]));
  const pickup = pointByKind.get("pickup");
  const dropoff = pointByKind.get("dropoff");
  if (pickup && dropoff) {
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", pickup.x);
    line.setAttribute("y1", pickup.y);
    line.setAttribute("x2", dropoff.x);
    line.setAttribute("y2", dropoff.y);
    overlay.append(line);
  }
  for (const point of points) {
    const position = pointByKind.get(point.kind);
    const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    circle.setAttribute("cx", position.x);
    circle.setAttribute("cy", position.y);
    circle.setAttribute("r", point.kind === "live" ? "8" : "6");
    circle.setAttribute("class", `map-${point.kind}`);
    overlay.append(circle);
  }
}

function renderTimeline(events) {
  const timeline = byId("tracking-timeline");
  timeline.replaceChildren();
  for (const [index, event] of events.entries()) {
    const item = document.createElement("li");
    if (index === events.length - 1) item.classList.add("current");
    const title = document.createElement("strong");
    title.textContent = statusLabels[event.status] ?? "Shipment updated";
    const time = document.createElement("time");
    time.dateTime = event.updated_at;
    time.textContent = formatDate(event.updated_at);
    item.append(title, time);
    timeline.append(item);
  }
}

function renderShipment(shipment) {
  byId("tracking-reference").textContent = `Reference ${shipment.reference}`;
  const status = byId("tracking-status");
  status.className = `status-pill ${statusClasses[shipment.status] ?? ""}`;
  status.textContent = statusLabels[shipment.status] ?? "Shipment updated";
  byId("tracking-pickup").textContent = shipment.pickup;
  byId("tracking-dropoff").textContent = shipment.dropoff;
  byId("tracking-updated").textContent = `Last update · ${formatDate(shipment.updated_at)}`;
  byId("customer-content").hidden = false;
  byId("tracking-error").hidden = true;
  renderMap(shipment);
  renderTimeline(shipment.events ?? []);
}

async function refresh() {
  if (requestActive || !token) return;
  requestActive = true;
  try {
    const response = await fetch("/customer/tracking", {
      cache: "no-store",
      headers: {
        Accept: "application/json",
        "X-Customer-Tracking-Token": token,
      },
      referrerPolicy: "no-referrer",
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error?.message ?? "Unable to load this tracking link.");
    renderShipment(result);
  } catch (error) {
    byId("tracking-reference").textContent = "";
    byId("tracking-error").textContent = error.message;
    byId("tracking-error").hidden = false;
    byId("customer-content").hidden = true;
    byId("tracking-status").textContent = "Unavailable";
    byId("tracking-status").className = "status-pill status-cancelled";
  } finally {
    requestActive = false;
  }
}

if (token) {
  refresh();
} else {
  byId("tracking-reference").textContent = "";
  byId("tracking-error").textContent = "This tracking link is missing its access code. Please use the complete link shared by the sender.";
  byId("tracking-error").hidden = false;
  byId("tracking-status").textContent = "Unavailable";
  byId("tracking-status").className = "status-pill status-cancelled";
}
window.setInterval(refresh, 15_000);
