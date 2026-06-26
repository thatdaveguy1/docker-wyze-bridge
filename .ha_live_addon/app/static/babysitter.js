/**
 * babysitter.js — Client-side logic for the Camera Babysitter settings page.
 *
 * Fetches status, handles all button clicks, auto-refreshes every 30s,
 * and uses bulma-toast for notifications.
 */

const API_BASE = "/babysitter/api";
const REFRESH_MS = 30000;
let refreshTimer = null;
let currentConfig = null;

// ---------------------------------------------------------------------------
// Toast helper (bulma-toast is loaded by base.html)
// ---------------------------------------------------------------------------
function toast(message, type = "is-info") {
  if (typeof bulmaToast !== "undefined") {
    bulmaToast.toast({ message, type, dismissible: true, duration: 4000 });
  } else {
    console.log(`[toast:${type}] ${message}`);
  }
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------
async function apiGet(path) {
  const resp = await fetch(`${API_BASE}${path}`);
  return resp.json();
}

async function apiPost(path, body = null) {
  const opts = { method: "POST" };
  if (body) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(`${API_BASE}${path}`, opts);
  return { status: resp.status, data: await resp.json() };
}

async function apiPut(path, body) {
  const resp = await fetch(`${API_BASE}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return { status: resp.status, data: await resp.json() };
}

// ---------------------------------------------------------------------------
// State badge helpers
// ---------------------------------------------------------------------------
function stateBadgeClass(state) {
  switch (state) {
    case "online":
      return "is-success";
    case "video_down":
      return "is-danger";
    case "snapshot_down":
      return "is-warning";
    case "wifi_down":
      return "is-dark";
    case "recovering":
      return "is-info";
    default:
      return "is-light";
  }
}

function stateLabel(state) {
  switch (state) {
    case "online":
      return "ONLINE";
    case "video_down":
      return "VIDEO DOWN";
    case "snapshot_down":
      return "SNAPSHOT DOWN";
    case "wifi_down":
      return "WIFI DOWN";
    case "recovering":
      return "RECOVERING";
    default:
      return state || "UNKNOWN";
  }
}

function formatTime(epoch) {
  if (!epoch) return "—";
  return new Date(epoch * 1000).toLocaleString();
}

// ---------------------------------------------------------------------------
// Render status cards
// ---------------------------------------------------------------------------
function renderStatusCards(statuses) {
  const container = document.getElementById("status-cards");
  if (!container) return;
  const names = Object.keys(statuses);
  if (names.length === 0) {
    container.innerHTML =
      '<div class="column"><p class="has-text-grey">No cameras configured.</p></div>';
    return;
  }
  container.innerHTML = names
    .map((name) => {
      const s = statuses[name];
      const badgeClass = stateBadgeClass(s.state);
      const label = stateLabel(s.state);
      const snapshotText = s.snapshot_valid
        ? "valid"
        : s.snapshot_hash
          ? "failed"
          : "stale";
      const snapshotColor = s.snapshot_valid
        ? "has-text-success"
        : "has-text-danger";
      const tcpText = s.tcp_reachable ? "yes" : "no";
      const tcpColor = s.tcp_reachable
        ? "has-text-success"
        : "has-text-danger";
      const cooldownText =
        s.cooldown_remaining > 0
          ? `${s.cooldown_remaining}s remaining`
          : "none";

      return `
      <div class="column is-4">
        <div class="card">
          <header class="card-header">
            <p class="card-header-title has-text-white-ter">
              ${name}
            </p>
            <span class="card-header-icon">
              <span class="tag ${badgeClass}">${label}</span>
            </span>
          </header>
          <div class="card-content">
            <div class="content is-small">
              <div class="level is-mobile">
                <div class="level-item">
                  <div>
                    <p class="heading">Camera FPS</p>
                    <p class="subtitle is-6">${s.camera_fps.toFixed(1)}</p>
                  </div>
                </div>
                <div class="level-item">
                  <div>
                    <p class="heading">Process FPS</p>
                    <p class="subtitle is-6">${s.process_fps.toFixed(1)}</p>
                  </div>
                </div>
                <div class="level-item">
                  <div>
                    <p class="heading">Skipped FPS</p>
                    <p class="subtitle is-6">${s.skipped_fps.toFixed(1)}</p>
                  </div>
                </div>
              </div>
              <hr class="my-2" />
              <p><strong>Snapshot:</strong> <span class="${snapshotColor}">${snapshotText}</span></p>
              <p><strong>TCP 554:</strong> <span class="${tcpColor}">${tcpText}</span></p>
              <p><strong>Last Reboot:</strong> ${formatTime(s.last_reboot)}</p>
              <p><strong>Reboots Today:</strong> ${s.reboots_today} / ${currentConfig?.max_daily ?? "?"}</p>
              <p><strong>Cooldown:</strong> ${cooldownText}</p>
            </div>
          </div>
        </div>
      </div>`;
    })
    .join("");
}

// ---------------------------------------------------------------------------
// Render controls table
// ---------------------------------------------------------------------------
function renderControlsTable(config) {
  const tbody = document.getElementById("controls-table-body");
  if (!tbody || !config.cameras) return;
  tbody.innerHTML = config.cameras
    .map((cam) => {
      const approved = (config.approved_cameras || []).includes(cam.friendly_name);
      const dryOverride = (config.per_camera_dry_run || {})[cam.friendly_name] || false;
      return `
      <tr>
        <td><strong>${cam.friendly_name}</strong><br /><small class="has-text-grey">${cam.ip}</small></td>
        <td>
          <button class="button is-small ${approved ? "is-success" : "is-light"}"
                  data-action="approve" data-camera="${cam.friendly_name}">
            <span class="icon"><i class="fas ${approved ? "fa-check-circle" : "fa-circle"}"></i></span>
            <span>${approved ? "Approved" : "Not Approved"}</span>
          </button>
        </td>
        <td>
          <button class="button is-small ${dryOverride ? "is-warning" : "is-light"}"
                  data-action="dryrun-camera" data-camera="${cam.friendly_name}">
            <span class="icon"><i class="fas fa-flask"></i></span>
            <span>${dryOverride ? "Override On" : "No Override"}</span>
          </button>
        </td>
        <td>
          <button class="button is-small is-danger"
                  data-action="reboot" data-camera="${cam.friendly_name}">
            <span class="icon"><i class="fas fa-power-off"></i></span>
            <span>Reboot Now</span>
          </button>
        </td>
      </tr>`;
    })
    .join("");
}

// ---------------------------------------------------------------------------
// Render config form
// ---------------------------------------------------------------------------
function renderConfigForm(config) {
  currentConfig = config;

  // Numeric fields
  const numFields = [
    "cooldown", "max_daily", "video_down_threshold", "snapshot_samples",
    "snapshot_stale_window", "recovery_wait", "reboot_timeout", "interval",
    "mqtt_port",
  ];
  numFields.forEach((f) => {
    const el = document.getElementById(`cfg-${f}`);
    if (el) el.value = config[f] ?? "";
  });

  // Text fields
  const textFields = [
    "scrypted_host", "scrypted_username", "scrypted_password",
    "frigate_host", "mqtt_broker", "mqtt_username", "mqtt_password",
    "reolink_username", "reolink_password",
  ];
  textFields.forEach((f) => {
    const el = document.getElementById(`cfg-${f}`);
    if (el) {
      // Passwords show as **** from the API; keep the placeholder.
      if (f.endsWith("_password") && config[f] === "****") {
        el.value = "";
        el.placeholder = "****";
      } else {
        el.value = config[f] ?? "";
      }
    }
  });

  // Camera mapping table
  const camBody = document.getElementById("camera-mapping-body");
  if (camBody && config.cameras) {
    camBody.innerHTML = config.cameras
      .map(
        (cam, i) => `
      <tr data-cam-index="${i}">
        <td><input class="input is-small" type="text" data-field="friendly_name" value="${cam.friendly_name}" /></td>
        <td><input class="input is-small" type="text" data-field="scrypted_id" value="${cam.scrypted_id}" /></td>
        <td><input class="input is-small" type="text" data-field="ip" value="${cam.ip}" /></td>
        <td><input class="input is-small" type="text" data-field="frigate_name" value="${cam.frigate_name}" /></td>
      </tr>`,
      )
      .join("");
  }
}

// ---------------------------------------------------------------------------
// Render history table
// ---------------------------------------------------------------------------
function renderHistoryTable(history) {
  const tbody = document.getElementById("history-table-body");
  if (!tbody) return;
  if (!history || history.length === 0) {
    tbody.innerHTML =
      '<tr><td colspan="6" class="has-text-grey has-text-centered">No reboot events recorded.</td></tr>';
    return;
  }
  tbody.innerHTML = history
    .map((e) => {
      const outcomeClass =
        e.outcome === "success" ? "has-text-success" : "has-text-danger";
      return `
      <tr>
        <td>${formatTime(e.timestamp)}</td>
        <td>${e.camera}</td>
        <td>${e.action}</td>
        <td>${e.reason}</td>
        <td class="${outcomeClass}"><strong>${e.outcome}</strong></td>
        <td>${e.duration.toFixed(1)}</td>
      </tr>`;
    })
    .join("");
}

// ---------------------------------------------------------------------------
// Update dry-run badge
// ---------------------------------------------------------------------------
function updateDryRunBadge(config) {
  const badge = document.getElementById("dry-run-badge");
  if (!badge) return;
  if (config.dry_run) {
    badge.textContent = "DRY-RUN";
    badge.className = "tag is-warning is-medium";
  } else {
    badge.textContent = "LIVE";
    badge.className = "tag is-danger is-medium";
  }
}

// ---------------------------------------------------------------------------
// Fetch and render everything
// ---------------------------------------------------------------------------
async function fetchAndRenderAll() {
  try {
    const [statuses, config, history] = await Promise.all([
      apiGet("/status"),
      apiGet("/config"),
      apiGet("/history"),
    ]);
    renderStatusCards(statuses);
    renderControlsTable(config);
    renderConfigForm(config);
    renderHistoryTable(history);
    updateDryRunBadge(config);
  } catch (err) {
    console.error("fetchAndRenderAll error:", err);
    toast("Failed to load babysitter data", "is-danger");
  }
}

// ---------------------------------------------------------------------------
// Button handlers
// ---------------------------------------------------------------------------
async function handleApprove(camera) {
  const { status, data } = await apiPost(`/approve/${camera}`);
  if (status === 200) {
    toast(`${camera}: ${data.approved ? "approved" : "unapproved"}`, "is-success");
    await fetchAndRenderAll();
  } else {
    toast(`Failed to toggle approval: ${data.error || "unknown"}`, "is-danger");
  }
}

async function handleDryRunGlobal() {
  const { status, data } = await apiPost("/dryrun");
  if (status === 200) {
    toast(data.message, data.dry_run ? "is-warning" : "is-success");
    await fetchAndRenderAll();
  } else {
    toast("Failed to toggle dry-run", "is-danger");
  }
}

async function handleDryRunCamera(camera) {
  const { status, data } = await apiPost(`/dryrun/${camera}`);
  if (status === 200) {
    toast(data.message, data.dry_run ? "is-warning" : "is-success");
    await fetchAndRenderAll();
  } else {
    toast(`Failed to toggle dry-run for ${camera}`, "is-danger");
  }
}

async function handleReboot(camera) {
  if (
    !confirm(
      `Are you sure you want to reboot ${camera} now?\n\nThis will send a reboot command to the camera via the Reolink CGI API.`,
    )
  ) {
    return;
  }
  const { status, data } = await apiPost(`/reboot/${camera}`);
  if (status === 200) {
    if (data.dry_run) {
      toast(data.message, "is-warning");
    } else {
      toast(`${camera}: reboot ${data.outcome} (${data.duration}s)`, "is-success");
    }
    await fetchAndRenderAll();
  } else if (status === 403) {
    toast(`${camera}: not approved for reboot`, "is-danger");
  } else if (status === 429) {
    toast(`${camera}: ${data.error}`, "is-warning");
  } else {
    toast(`${camera}: reboot failed — ${data.error || "unknown"}`, "is-danger");
  }
}

async function handleDiscover() {
  const btn = document.getElementById("btn-discover");
  if (btn) {
    btn.disabled = true;
    btn.classList.add("is-loading");
  }
  try {
    const { status, data } = await apiPost("/discover");
    if (status === 200) {
      const resultsDiv = document.getElementById("discovery-results");
      const jsonPre = document.getElementById("discovery-json");
      if (resultsDiv && jsonPre) {
        jsonPre.textContent = JSON.stringify(data, null, 2);
        resultsDiv.classList.remove("is-hidden");
      }
      toast("Discovery completed", "is-success");
    } else {
      toast(`Discovery failed: ${data.error || "unknown"}`, "is-danger");
    }
  } catch (err) {
    toast(`Discovery error: ${err}`, "is-danger");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.classList.remove("is-loading");
    }
  }
}

// ---------------------------------------------------------------------------
// Config form submit
// ---------------------------------------------------------------------------
async function handleConfigSubmit(e) {
  e.preventDefault();

  // Gather numeric + text fields.
  const updates = {};
  const numFields = [
    "cooldown", "max_daily", "video_down_threshold", "snapshot_samples",
    "snapshot_stale_window", "recovery_wait", "reboot_timeout", "interval",
    "mqtt_port",
  ];
  numFields.forEach((f) => {
    const el = document.getElementById(`cfg-${f}`);
    if (el && el.value !== "") updates[f] = parseInt(el.value, 10);
  });

  const textFields = [
    "scrypted_host", "scrypted_username", "scrypted_password",
    "frigate_host", "mqtt_broker", "mqtt_username", "mqtt_password",
    "reolink_username", "reolink_password",
  ];
  textFields.forEach((f) => {
    const el = document.getElementById(`cfg-${f}`);
    if (el && el.value !== "") {
      updates[f] = el.value;
    } else if (f.endsWith("_password") && el && el.value === "") {
      // Keep masked placeholder — don't send.
    }
  });

  // Gather camera mapping.
  const camRows = document.querySelectorAll("#camera-mapping-body tr");
  const cameras = [];
  camRows.forEach((row) => {
    const cam = {};
    row.querySelectorAll("input[data-field]").forEach((input) => {
      cam[input.dataset.field] = input.value;
    });
    if (cam.friendly_name) cameras.push(cam);
  });
  if (cameras.length > 0) updates.cameras = cameras;

  const { status, data } = await apiPut("/config", updates);
  if (status === 200) {
    toast("Configuration saved", "is-success");
    renderConfigForm(data);
    renderControlsTable(data);
    updateDryRunBadge(data);
  } else {
    toast(`Failed to save config: ${data.error || "unknown"}`, "is-danger");
  }
}

// ---------------------------------------------------------------------------
// Event delegation for button clicks
// ---------------------------------------------------------------------------
document.addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-action]");
  if (!btn) return;
  const action = btn.dataset.action;
  const camera = btn.dataset.camera;
  switch (action) {
    case "approve":
      handleApprove(camera);
      break;
    case "dryrun-camera":
      handleDryRunCamera(camera);
      break;
    case "reboot":
      handleReboot(camera);
      break;
  }
});

// ---------------------------------------------------------------------------
// Init on page load
// ---------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  // Global dry-run button.
  const dryBtn = document.getElementById("btn-global-dryrun");
  if (dryBtn) dryBtn.addEventListener("click", handleDryRunGlobal);

  // Discover button.
  const discBtn = document.getElementById("btn-discover");
  if (discBtn) discBtn.addEventListener("click", handleDiscover);

  // Config form.
  const cfgForm = document.getElementById("config-form");
  if (cfgForm) cfgForm.addEventListener("submit", handleConfigSubmit);

  // Initial fetch + auto-refresh.
  fetchAndRenderAll();
  refreshTimer = setInterval(fetchAndRenderAll, REFRESH_MS);
});
