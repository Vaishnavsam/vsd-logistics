const byId = (id) => document.getElementById(id);
const tokenInput = byId("driver-token");
let watchId = null;
let sendTimer = null;
let latestPosition = null;
let lastSentAt = 0;
let requestInFlight = false;
let requestController = null;

tokenInput.value = sessionStorage.getItem("vsd-driver-token") ?? "";

function setStatus(message, isError = false) {
  const status = byId("driver-status");
  status.textContent = message;
  status.classList.toggle("error", isError);
}

function stopSharing(message = "Location sharing is off.") {
  if (watchId !== null) navigator.geolocation.clearWatch(watchId);
  if (sendTimer !== null) clearInterval(sendTimer);
  requestController?.abort();
  requestController = null;
  watchId = null;
  sendTimer = null;
  latestPosition = null;
  requestInFlight = false;
  byId("start-sharing").disabled = false;
  byId("stop-sharing").disabled = true;
  setStatus(message);
}

async function sendLatestPosition() {
  if (!latestPosition || requestInFlight || Date.now() - lastSentAt < 14_000) return;
  if (Date.now() - latestPosition.timestamp > 60_000) {
    setStatus("The device location fix is stale. Waiting for a fresh GPS reading.");
    return;
  }
  lastSentAt = Date.now();
  requestInFlight = true;
  const controller = new AbortController();
  requestController = controller;
  const { latitude, longitude, accuracy } = latestPosition.coords;
  try {
    const response = await fetch("/driver/location", {
      method: "POST",
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        "X-Driver-Token": tokenInput.value.trim(),
      },
      body: JSON.stringify({
        latitude,
        longitude,
        accuracy_m: accuracy,
      }),
    });
    const result = await response.json();
    if (!response.ok) {
      const error = new Error(result.error?.message ?? `Location update failed (${response.status}).`);
      error.code = result.error?.code;
      throw error;
    }
    byId("fix-coordinates").textContent = `${result.latitude.toFixed(5)}, ${result.longitude.toFixed(5)}`;
    byId("fix-accuracy").textContent = result.accuracy_m == null
      ? "Device accuracy was not provided."
      : `Reported accuracy: ${Math.round(result.accuracy_m)} m`;
    byId("fix-time").textContent = `Received by operations: ${new Date(result.recorded_at).toLocaleString()}`;
    byId("driver-fix").hidden = false;
    setStatus("Location shared with operations.");
  } catch (error) {
    if (error.name === "AbortError") return;
    setStatus(error.message, true);
    if (error.code === "invalid_driver_token" || error.code === "driver_not_in_transit") {
      stopSharing(error.message);
    }
  } finally {
    requestInFlight = false;
    if (requestController === controller) requestController = null;
  }
}

byId("start-sharing").addEventListener("click", () => {
  const token = tokenInput.value.trim();
  if (!token) {
    setStatus("Enter the driver access token provided by your operator.", true);
    tokenInput.focus();
    return;
  }
  if (!navigator.geolocation) {
    setStatus("This browser does not support device location.", true);
    return;
  }
  sessionStorage.setItem("vsd-driver-token", token);
  byId("start-sharing").disabled = true;
  byId("stop-sharing").disabled = false;
  setStatus("Waiting for device location permission and a GPS fix…");
  watchId = navigator.geolocation.watchPosition(
    (position) => {
      latestPosition = position;
      setStatus("Location available. Sending secure updates every 15 seconds.");
      sendLatestPosition();
    },
    (error) => {
      const messages = {
        1: "Location permission was denied. Allow location access in your browser settings to continue.",
        2: "Device location is unavailable. Check GPS/location services and try again.",
        3: "The location request timed out. Waiting for another device fix.",
      };
      setStatus(messages[error.code] ?? "Unable to read device location.", true);
    },
    { enableHighAccuracy: true, maximumAge: 10_000, timeout: 20_000 },
  );
  sendTimer = setInterval(sendLatestPosition, 15_000);
});

byId("stop-sharing").addEventListener("click", () => stopSharing());
byId("clear-token").addEventListener("click", () => {
  sessionStorage.removeItem("vsd-driver-token");
  tokenInput.value = "";
  stopSharing("Saved token removed. Location sharing is off.");
});
