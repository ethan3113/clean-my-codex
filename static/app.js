const state = {
  productName: "Clean My Codex",
  requestToken: "",
  page: "chats",
  chats: [],
  selectedChats: new Set(),
  paths: null,
  missingPaths: [],
  cleanerItems: [],
  cleanerSummary: null,
  cleanerSelected: new Set(),
  trashBin: [],
  logs: [],
  focusedChatId: null,
  focusedMissingPath: null,
  focusedTrashItemId: null,
  focusedCleanerPath: null,
  focusedLogIndex: null,
  activeScans: 0,
  destructiveActionActive: false,
  relocationPreview: null,
  requests: {
    chats: 0,
    missing: 0,
    cleaner: 0,
    trash: 0,
    logs: 0,
  },
};

const pageMeta = {
  overview: ["Overview", "Read-only discovery and safety status."],
  chats: ["Chat Manager", "Browse, review, and remove Codex chat sessions."],
  missing: ["Missing Paths", "Review project locations Codex can no longer find."],
  relocation: ["Relocation Manager", "Repair references after a workspace is moved or renamed."],
  cleaner: ["Folder Cleaner", "Inspect stale and backup-looking files without touching active data."],
  trashbin: ["Trash Bin", "Restore deleted Codex records or remove Trash items permanently."],
  logs: ["Operation Logs", "Review changes and verification results."],
};

const $ = (id) => document.getElementById(id);

function consumeSessionToken() {
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const token = String(params.get("token") || "").trim();
  if (token) state.requestToken = token;
  if (window.location.hash) {
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
  }
}

async function api(path, options = {}) {
  const method = String(options.method || "GET").toUpperCase();
  const isApi = path.startsWith("/api/");
  if (isApi && path !== "/api/health" && !state.requestToken) {
    throw new Error("Open Clean My Codex from its launcher to start a protected app session");
  }
  const init = {
    method,
    headers: { Accept: "application/json" },
  };
  if (options.body) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  if (isApi && state.requestToken) {
    init.headers["X-Clean-My-Codex-Token"] = state.requestToken;
  }
  const response = await fetch(path, init);
  const text = await response.text();
  let payload = {};
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    payload = { error: text || "Invalid JSON response" };
  }
  if (!response.ok) {
    throw new Error(payload.error || `Request failed: ${response.status}`);
  }
  return payload;
}

function asJson(value) {
  return JSON.stringify(value, null, 2);
}

function setPre(id, value) {
  $(id).textContent = typeof value === "string" ? value : asJson(value);
}

function toast(message) {
  const node = $("toast");
  node.textContent = message;
  node.hidden = false;
  clearTimeout(node.timer);
  node.timer = setTimeout(() => {
    node.hidden = true;
  }, 3600);
}

function formatTime(value) {
  if (!value) return "-";
  if (typeof value === "number" && value > 10_000_000_000) {
    return new Date(value).toLocaleString();
  }
  if (typeof value === "string") {
    const date = new Date(value);
    if (!Number.isNaN(date.getTime())) return date.toLocaleString();
  }
  return String(value);
}

function bytes(value) {
  const size = Number(value || 0);
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function text(value) {
  return value === undefined || value === null || value === "" ? "-" : String(value);
}

function pause(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function runControlledAction(work) {
  if (state.destructiveActionActive) {
    throw new Error("Another confirmed action is already in progress");
  }
  state.destructiveActionActive = true;
  const shell = document.querySelector(".shell");
  if (shell) shell.inert = true;
  document.body.setAttribute("aria-busy", "true");
  try {
    return await work();
  } finally {
    state.destructiveActionActive = false;
    if (shell) shell.inert = false;
    document.body.removeAttribute("aria-busy");
  }
}

function el(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = content;
  return node;
}

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#icon-${name}`);
  svg.appendChild(use);
  return svg;
}

const statusIconNames = {
  ok: "status-ok",
  warn: "status-warn",
  bad: "status-bad",
  info: "status-info",
  muted: "status-muted",
  syncing: "status-sync",
};

function statusIcon(kind = "muted") {
  const node = icon(statusIconNames[kind] || statusIconNames.muted);
  node.classList.add("status-icon");
  node.setAttribute("aria-hidden", "true");
  return node;
}

function compactPath(value) {
  const path = text(value);
  return path.replace(/^\/Users\/[^/]+(?=\/|$)/, "~");
}

function shortId(value) {
  const id = text(value);
  if (id.length <= 18) return id;
  return `${id.slice(0, 8)}...${id.slice(-6)}`;
}

function concise(value, limit = 160) {
  const normalized = text(value).replace(/\s+/g, " ").trim();
  return normalized.length > limit ? `${normalized.slice(0, Math.max(1, limit - 3))}...` : normalized;
}

function chatDisplayName(chat) {
  const indexed = String(chat?.display_title || chat?.indexed_title || "").replace(/\s+/g, " ").trim();
  if (indexed) return concise(indexed, 84);
  const id = String(chat?.id || "").trim();
  return id ? `Untitled chat - ${id.slice(0, 8)}` : "Untitled chat";
}

function skeletonLine(width = "100%", className = "") {
  const node = el("span", `skeleton-line${className ? ` ${className}` : ""}`);
  node.style.setProperty("--skeleton-width", width);
  return node;
}

function setButtonLoading(id, loading, loadingLabel = "Scanning") {
  const button = $(id);
  if (!button) return;
  button.classList.toggle("is-loading", loading);
  button.disabled = loading;
  button.setAttribute("aria-busy", loading ? "true" : "false");
  const label = button.querySelector("span");
  if (!label) return;
  if (!button.dataset.idleLabel) button.dataset.idleLabel = label.textContent;
  label.textContent = loading ? loadingLabel : button.dataset.idleLabel;
}

function setConnectionState(mode) {
  const node = $("health-pill");
  if (!node) return;
  const states = {
    connecting: ["Connecting to Codex", "status-pill syncing", "syncing"],
    scanning: ["Scanning Codex", "status-pill syncing", "syncing"],
    connected: ["Connected to Codex", "status-pill ok", "ok"],
    issue: ["Codex needs attention", "status-pill warn", "warn"],
    unavailable: ["Codex unavailable", "status-pill bad", "bad"],
  };
  const [label, className, kind] = states[mode] || states.connecting;
  node.className = className;
  node.replaceChildren(statusIcon(kind), document.createTextNode(label));
}

function setProductMeta(meta = {}) {
  const productName = String(meta.name || "Clean My Codex");
  state.productName = productName;
  const nameNode = $("app-name");
  if (nameNode) nameNode.textContent = productName;
  const version = String(meta.version || "0.1.0").replace(/^v/i, "");
  const versionNode = $("app-version");
  if (versionNode) versionNode.textContent = `v${version}`;

  const issueLink = $("issue-link");
  const issuesUrl = String(
    meta.issues_url || "https://github.com/ethan3113/clean-my-codex/issues/new?template=bug_report.yml",
  );
  if (issueLink && issuesUrl.startsWith("https://github.com/") && issuesUrl.includes("/issues")) {
    issueLink.href = issuesUrl;
  }

  const creatorLink = $("creator-link");
  if (!creatorLink) return;
  const creator = String(meta.creator || "ENVOCS Studio");
  const creatorUrl = String(meta.creator_url || "https://github.com/ethan3113");
  const label = creatorLink.querySelector("span");
  if (label) label.textContent = `Created by ${creator}`;
  if (creatorUrl.startsWith("https://github.com/")) creatorLink.href = creatorUrl;
  creatorLink.setAttribute("aria-label", `${creator} on GitHub`);
}

function beginScan(buttonId, loadingLabel = "Scanning") {
  state.activeScans += 1;
  setButtonLoading(buttonId, true, loadingLabel);
  setConnectionState("scanning");
}

function endScan(buttonId, keepButtonLoading = false) {
  state.activeScans = Math.max(0, state.activeScans - 1);
  if (!keepButtonLoading) setButtonLoading(buttonId, false);
  if (state.activeScans === 0) setConnectionState("connected");
}

function renderDetailSkeleton(id, count = 5) {
  const root = $(id);
  if (!root) return;
  root.replaceChildren();
  for (let index = 0; index < count; index += 1) {
    const row = el("div", "detail-row skeleton-detail-row");
    const term = el("dt");
    const description = el("dd");
    term.appendChild(skeletonLine(index % 2 ? "62px" : "76px"));
    description.appendChild(skeletonLine(index % 3 ? "78%" : "92%"));
    row.append(term, description);
    root.appendChild(row);
  }
}

function renderChatSkeleton(count = 8) {
  const body = $("chat-rows");
  body.replaceChildren();
  body.setAttribute("aria-busy", "true");
  $("chat-result-count").textContent = "Scanning";
  for (let index = 0; index < count; index += 1) {
    const row = el("tr", "skeleton-table-row");
    row.setAttribute("aria-hidden", "true");
    const check = el("td", "check-col");
    check.appendChild(el("span", "skeleton-box"));
    const title = el("td", "skeleton-stack");
    title.append(skeletonLine(index % 3 ? "66%" : "82%"), skeletonLine("34%"));
    const workspace = el("td");
    workspace.appendChild(skeletonLine(index % 2 ? "72%" : "88%"));
    const updated = el("td");
    updated.appendChild(skeletonLine("74%"));
    const health = el("td", "skeleton-health");
    health.append(el("span", "skeleton-status-icon"), skeletonLine("58px"));
    row.append(check, title, workspace, updated, health);
    body.appendChild(row);
  }
  if (state.chats.length === 0) {
    $("chat-detail-title").replaceChildren(skeletonLine("78%"));
    $("chat-detail-id").replaceChildren(skeletonLine("46%"));
    setStatusLine("chat-detail-status", "Scanning", "info");
    renderDetailSkeleton("chat-detail-list");
  }
}

function renderMissingSkeleton(count = 7) {
  const root = $("missing-path-cleaner");
  if (!root) return;
  root.replaceChildren();
  root.setAttribute("aria-busy", "true");
  $("missing-result-count").textContent = "Scanning";
  for (let index = 0; index < count; index += 1) {
    const row = el("div", "missing-row skeleton-list-row");
    row.setAttribute("aria-hidden", "true");
    row.append(
      skeletonLine(index % 2 ? "68%" : "84%"),
      skeletonLine("24px"),
      skeletonLine("24px"),
      skeletonLine("90px"),
      skeletonLine("52px", "skeleton-control"),
    );
    root.appendChild(row);
  }
}

function renderCleanerSkeleton(count = 8) {
  const body = $("cleaner-rows");
  if (!body) return;
  body.replaceChildren();
  body.setAttribute("aria-busy", "true");
  ["cleaner-score", "cleaner-files", "cleaner-stale", "cleaner-unknown"].forEach((id) => {
    $(id).replaceChildren(skeletonLine("42px"));
  });
  for (let index = 0; index < count; index += 1) {
    const row = el("tr", "skeleton-table-row");
    row.setAttribute("aria-hidden", "true");
    const check = el("td", "check-col");
    check.appendChild(el("span", "skeleton-box"));
    const file = el("td", "skeleton-stack");
    file.append(skeletonLine(index % 2 ? "64%" : "78%"), skeletonLine("42%"));
    const classification = el("td", "skeleton-stack");
    classification.append(skeletonLine("72px"), skeletonLine("58%"));
    const size = el("td");
    size.appendChild(skeletonLine("48px"));
    const modified = el("td");
    modified.appendChild(skeletonLine("76%"));
    const action = el("td");
    action.appendChild(skeletonLine("52px", "skeleton-control"));
    row.append(check, file, classification, size, modified, action);
    body.appendChild(row);
  }
}

function renderListSkeleton(rootId, count = 6) {
  const root = $(rootId);
  if (!root) return;
  root.replaceChildren();
  root.setAttribute("aria-busy", "true");
  for (let index = 0; index < count; index += 1) {
    const row = el("div", "list-item skeleton-list-item");
    row.setAttribute("aria-hidden", "true");
    const content = el("div", "skeleton-stack");
    content.append(skeletonLine(index % 2 ? "58%" : "72%"), skeletonLine("34%"));
    row.append(content, skeletonLine("52px", "skeleton-control"));
    root.appendChild(row);
  }
}

function formatCompactTime(value) {
  if (!value) return "-";
  const normalized = typeof value === "number" && value < 10_000_000_000 ? value * 1000 : value;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return text(value);
  return date.toLocaleString([], {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function statusNode(label, kind = "muted") {
  const node = el("span", `health-cell ${kind}`);
  node.appendChild(statusIcon(kind));
  node.appendChild(document.createTextNode(label));
  return node;
}

function setStatusLine(id, label, kind = "muted") {
  const node = $(id);
  if (!node) return;
  node.className = `status-line ${kind}-status`;
  node.replaceChildren(statusIcon(kind), document.createTextNode(label));
}

function renderDetailList(id, rows) {
  const root = $(id);
  if (!root) return;
  root.replaceChildren();
  rows.forEach(([label, value, className = ""]) => {
    const wrapper = el("div", "detail-row");
    wrapper.appendChild(el("dt", "", label));
    wrapper.appendChild(el("dd", className, text(value)));
    root.appendChild(wrapper);
  });
}

function openDetails(id) {
  const details = $(id);
  if (details) details.open = true;
}

function countSqliteRows(sqlite) {
  return Object.values(sqlite || {}).reduce((total, database) => {
    return total + Object.values(database?.rows_to_delete || {}).reduce((sum, count) => sum + Number(count || 0), 0);
  }, 0);
}

function playNavMotion(button) {
  if (!button || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const token = `${Date.now()}-${Math.random()}`;
  button.dataset.motionToken = token;
  button.classList.remove("is-animating");
  window.requestAnimationFrame(() => {
    window.requestAnimationFrame(() => {
      if (button.dataset.motionToken !== token) return;
      button.classList.add("is-animating");
      window.setTimeout(() => {
        if (button.dataset.motionToken === token) button.classList.remove("is-animating");
      }, 1000);
    });
  });
}

function setPage(page) {
  state.page = page;
  document.querySelectorAll(".nav-item").forEach((button) => {
    const isActive = button.dataset.page === page;
    button.classList.toggle("active", isActive);
    if (isActive) {
      button.setAttribute("aria-current", "page");
    } else {
      button.removeAttribute("aria-current");
    }
  });
  document.querySelectorAll(".page").forEach((section) => {
    section.classList.toggle("active", section.id === `${page}-page`);
  });
  $("page-title").textContent = pageMeta[page][0];
  $("page-subtitle").textContent = pageMeta[page][1];
  document.title = `${pageMeta[page][0]} - ${state.productName}`;
  if (page === "logs") loadLogs();
  if (page === "missing") loadMissingPaths().catch((error) => toast(error.message));
  if (page === "trashbin") loadTrashBin().catch((error) => toast(error.message));
  if (page === "cleaner") loadTrashBin().catch((error) => toast(error.message));
}

async function loadHealth({ updateStatus = true } = {}) {
  if (updateStatus) setConnectionState("connecting");
  try {
    const health = await api("/api/health");
    setProductMeta(health.app);
    if (updateStatus) setConnectionState(health.ok ? "connected" : "issue");
    return Boolean(health.ok);
  } catch (error) {
    if (updateStatus) setConnectionState("unavailable");
    toast(error.message);
    return false;
  }
}

async function loadOverview() {
  const data = await api("/api/discovery");
  $("metric-chats").textContent = data.chat_count;
  $("metric-active").textContent = data.active_chat_count;
  $("metric-archived").textContent = data.archived_chat_count;
  $("metric-missing").textContent = data.missing_path_count;
  renderPathList("managed-files", data.managed_active_files);
  renderPathList("never-touch", data.never_touch);
  state.paths = data.paths;
  renderPathFilters();
  renderMissingPaths();
}

function renderPathList(id, values) {
  const list = $(id);
  list.replaceChildren();
  (values || []).forEach((value) => {
    const item = el("li", "path", value);
    list.appendChild(item);
  });
}

async function loadChats() {
  const requestId = ++state.requests.chats;
  renderChatSkeleton();
  beginScan("chat-refresh", "Scanning");
  const params = new URLSearchParams();
  const search = $("chat-search").value.trim();
  const archived = $("chat-archived").value;
  const cwd = $("chat-cwd").value;
  if (search) params.set("search", search);
  if (archived) params.set("archived", archived);
  if (cwd) params.set("cwd", cwd);
  try {
    const [data] = await Promise.all([
      api(`/api/chats?${params.toString()}`),
      pause(240),
    ]);
    if (requestId !== state.requests.chats) return;
    state.chats = data.chats || [];
    state.selectedChats = new Set([...state.selectedChats].filter((id) => state.chats.some((chat) => chat.id === id)));
    if (!state.chats.some((chat) => chat.id === state.focusedChatId)) {
      state.focusedChatId = state.chats[0]?.id || null;
    }
    renderChats();
  } finally {
    endScan("chat-refresh", requestId !== state.requests.chats);
  }
}

async function loadPaths() {
  state.paths = await api("/api/paths");
  renderPathFilters();
  renderMissingPaths();
}

function renderPathFilters() {
  if (!state.paths) return;
  const select = $("chat-cwd");
  const current = select.value;
  select.replaceChildren(new Option("All projects", ""));
  (state.paths.cwd_paths || []).forEach((row) => {
    const label = `${row.exists ? "Available" : "Missing"} - ${compactPath(row.path)}`;
    select.appendChild(new Option(label, row.path));
  });
  select.value = current;
}

function renderChats() {
  const body = $("chat-rows");
  body.replaceChildren();
  body.removeAttribute("aria-busy");
  $("chat-result-count").textContent = `${state.chats.length} chat${state.chats.length === 1 ? "" : "s"}`;
  if (state.chats.length === 0) {
    const row = document.createElement("tr");
    const cell = el("td", "muted", "No chats match the current filters.");
    cell.colSpan = 5;
    row.appendChild(cell);
    body.appendChild(row);
    renderChatInspector(null);
    updateSelectionState();
    return;
  }
  state.chats.forEach((chat) => {
    const row = document.createElement("tr");
    row.dataset.chatId = chat.id;
    row.classList.toggle("is-focused", chat.id === state.focusedChatId);

    const checkCell = el("td", "check-col");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.dataset.threadId = chat.id;
    checkbox.checked = state.selectedChats.has(chat.id);
    const displayName = chatDisplayName(chat);
    checkbox.setAttribute("aria-label", `Select ${displayName}`);
    checkCell.appendChild(checkbox);

    const titleCell = el("td", "title-cell");
    titleCell.appendChild(el("strong", "", displayName));
    titleCell.appendChild(el("div", "muted mono", shortId(chat.id)));

    const projectCell = el("td");
    projectCell.appendChild(el("span", "path truncate-path", compactPath(chat.cwd || "-")));
    const updatedCell = el("td", "muted", formatCompactTime(chat.updated_at));
    const health = chatHealth(chat);
    const statusCell = el("td");
    statusCell.appendChild(statusNode(health.label, health.kind));

    row.addEventListener("click", (event) => {
      if (event.target.closest("input, button, a")) return;
      state.focusedChatId = chat.id;
      renderChats();
    });

    row.append(checkCell, titleCell, projectCell, updatedCell, statusCell);
    body.appendChild(row);
  });
  renderChatInspector(state.chats.find((chat) => chat.id === state.focusedChatId) || state.chats[0]);
  updateSelectionState();
}

function chatHealth(chat) {
  const badges = new Set(chat.badges || []);
  if (badges.has("Manual review needed")) return { label: "Review needed", kind: "bad" };
  if (!chat.cwd_exists) return { label: "Path missing", kind: "warn" };
  if (!chat.session_exists) return { label: "Session missing", kind: "warn" };
  if (chat.archived) return { label: "Archived", kind: "info" };
  if (badges.has("Already trashed") || badges.has("Fully detached")) return { label: "Trashed", kind: "info" };
  return { label: "Ready", kind: "ok" };
}

function renderChatInspector(chat) {
  if (!chat) {
    $("chat-detail-title").textContent = "No chat selected";
    $("chat-detail-id").textContent = "Adjust the filters or refresh the chat list.";
    setStatusLine("chat-detail-status", "Not selected", "muted");
    renderDetailList("chat-detail-list", []);
    setPre("chat-preview", "No technical references to show.");
    return;
  }
  const health = chatHealth(chat);
  $("chat-detail-title").textContent = chatDisplayName(chat);
  $("chat-detail-id").textContent = chat.id;
  setStatusLine("chat-detail-status", health.label, health.kind);
  const references = (chat.badges || []).filter((label) => !["Clean removable", "Fully detached"].includes(label));
  renderDetailList("chat-detail-list", [
    ["Workspace", compactPath(chat.cwd), "path"],
    ["Session file", compactPath(chat.rollout_path), "path"],
    ["Session", chat.session_exists ? `${bytes(chat.session_size)} available` : "File not found"],
    ["References", references.length ? references.join(", ") : "No issues detected"],
    ["Sources", (chat.sources || []).join(", ") || "-"],
    ["Updated", formatCompactTime(chat.updated_at)],
  ]);
  setPre("chat-preview", {
    thread_id: chat.id,
    workspace: chat.cwd,
    session_file: chat.rollout_path,
    sources: chat.sources || [],
    reference_flags: chat.badges || [],
  });
}

function badge(label, kind) {
  return el("span", `badge ${kind || ""}`, label);
}

function badgeKindForChatStatus(label) {
  if (label === "Clean removable") return "ok";
  if (label === "Fully detached") return "ok";
  if (label === "Already trashed") return "warn";
  if (label === "Manual review needed") return "bad";
  if (label === "Purge available") return "bad";
  if (label === "Metadata remains") return "warn";
  if (label === "Database-linked" || label === "References found") return "warn";
  return "";
}

function updateSelectionState() {
  const count = state.selectedChats.size;
  $("chat-selection-count").textContent = count ? `${count} selected` : "Select chats to manage";
  $("chat-selection-bar").classList.toggle("has-selection", count > 0);
  $("preview-trash").disabled = count === 0;
  $("apply-trash").disabled = count === 0;
  $("select-all-chats").checked = count > 0 && count === state.chats.length;
  $("select-all-chats").indeterminate = count > 0 && count < state.chats.length;
}

async function previewTrash(threadIds = [...state.selectedChats]) {
  const payload = await api("/api/chats/preview-delete", {
    method: "POST",
    body: { thread_ids: threadIds },
  });
  setPre("chat-preview", payload);
  const note = $("chat-operation-summary");
  note.hidden = false;
  note.replaceChildren(
    el("strong", "", "Delete preview ready"),
    document.createTextNode(`${threadIds.length} chat${threadIds.length === 1 ? "" : "s"} will move to Trash Bin with restore metadata before active references are detached.`),
  );
  openDetails("chat-technical-details");
  return payload;
}

async function applyTrash() {
  return runControlledAction(async () => {
    const threadIds = [...state.selectedChats];
    const preview = await previewTrash(threadIds);
    const names = (preview.threads || [])
      .map((thread) => thread.display_title || thread.title || thread.id)
      .slice(0, 3)
      .join(", ");
    await confirmAction({
      title: "Delete selected chats",
      message: `Selected: ${names || threadIds.join(", ")}. This moves related session files and restore metadata into Trash Bin, then removes proven active Codex references. Type DELETE CHAT.`,
      phrase: "DELETE CHAT",
      dangerous: true,
      run: async () => {
        const result = await api("/api/chats/delete", {
          method: "POST",
          body: {
            thread_ids: threadIds,
            preview_token: preview.preview_token,
            confirm: true,
            confirmation: "DELETE CHAT",
          },
        });
        setPre("chat-preview", result);
        state.selectedChats.clear();
        await Promise.all([loadChats(), loadTrashBin(), loadMissingPaths(), loadLogs()]);
        toast(`Delete result: ${result.status}`);
      },
    });
  });
}

function renderMissingPaths() {
  const root = $("missing-paths");
  if (!root || !state.paths) return;
  root.replaceChildren();
  const missing = state.paths.missing_paths || [];
  if (missing.length === 0) {
    root.appendChild(el("div", "missing-item muted", "No missing project paths detected."));
    return;
  }
  missing.forEach((row) => {
    const item = el("div", "missing-item");
    item.appendChild(el("strong", "path", compactPath(row.path)));
    item.appendChild(el("div", "muted", `${row.thread_count} thread(s)`));
    const actions = el("div", "list-actions");
    const use = el("button", "icon-button-label", "Use this path");
    use.prepend(icon("arrow-up-right"));
    use.addEventListener("click", () => {
      $("old-path").value = row.path;
      invalidateRelocationPreview();
      toast("Old path field updated.");
    });
    actions.appendChild(use);
    item.appendChild(actions);
    root.appendChild(item);
  });
}

async function loadMissingPaths() {
  const requestId = ++state.requests.missing;
  renderMissingSkeleton();
  beginScan("missing-scan", "Scanning");
  try {
    const [data] = await Promise.all([
      api("/api/missing-paths"),
      pause(240),
    ]);
    if (requestId !== state.requests.missing) return;
    state.missingPaths = data.missing_paths || [];
    if (!state.missingPaths.some((item) => item.path === state.focusedMissingPath)) {
      state.focusedMissingPath = state.missingPaths[0]?.path || null;
    }
    renderMissingPathCleaner();
  } finally {
    endScan("missing-scan", requestId !== state.requests.missing);
  }
}

function renderMissingPathCleaner() {
  const root = $("missing-path-cleaner");
  if (!root) return;
  root.replaceChildren();
  root.removeAttribute("aria-busy");
  const rows = filteredMissingPaths();
  $("missing-result-count").textContent = `${rows.length} path${rows.length === 1 ? "" : "s"}`;
  const unrecoverable = state.missingPaths.filter((row) => row.unrecoverable).length;
  $("missing-batch-copy").textContent = unrecoverable
    ? `${unrecoverable} unrecoverable path${unrecoverable === 1 ? "" : "s"} can be reviewed together.`
    : "No unrecoverable paths need cleanup.";
  $("missing-trash-all").disabled = unrecoverable === 0;
  if (rows.length === 0) {
    root.appendChild(el("div", "missing-item muted", state.missingPaths.length ? "No paths match the current filters." : "No missing project paths detected."));
    renderMissingPathInspector(null);
    return;
  }
  rows.forEach((row) => {
    const health = missingPathHealth(row);
    const item = el("div", "missing-row");
    item.tabIndex = 0;
    item.dataset.path = row.path;
    item.classList.toggle("is-focused", row.path === state.focusedMissingPath);
    item.appendChild(el("span", "path", compactPath(row.path)));
    item.appendChild(el("span", "row-number", row.chat_count || 0));
    item.appendChild(el("span", "row-number", (row.session_files || []).length));
    item.appendChild(statusNode(health.label, health.kind));
    const review = el("button", "row-action", "Review");
    review.addEventListener("click", (event) => {
      event.stopPropagation();
      state.focusedMissingPath = row.path;
      renderMissingPathCleaner();
    });
    item.appendChild(review);
    const focusRow = () => {
      state.focusedMissingPath = row.path;
      renderMissingPathCleaner();
    };
    item.addEventListener("click", focusRow);
    item.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        focusRow();
      }
    });
    root.appendChild(item);
  });
  renderMissingPathInspector(rows.find((row) => row.path === state.focusedMissingPath) || rows[0]);
}

function missingPathHealth(row) {
  if (!row.safe_to_delete || (row.preview?.manual_review || []).length) {
    return { label: "Manual review", kind: "bad", filter: "review" };
  }
  if (row.unrecoverable) return { label: "Cleanup available", kind: "warn", filter: "cleanup" };
  return { label: "Relink available", kind: "ok", filter: "relink" };
}

function filteredMissingPaths() {
  const search = $("missing-search")?.value.trim().toLowerCase() || "";
  const status = $("missing-status")?.value || "";
  return state.missingPaths.filter((row) => {
    const health = missingPathHealth(row);
    if (status && health.filter !== status) return false;
    if (!search) return true;
    return [row.path, row.label, ...(row.thread_ids || [])].some((value) => String(value || "").toLowerCase().includes(search));
  });
}

function renderMissingPathInspector(row) {
  const enabled = Boolean(row);
  ["missing-relink", "missing-preview-action", "missing-move-action", "missing-project-action"].forEach((id) => {
    $(id).disabled = !enabled;
  });
  if (!row) {
    $("missing-detail-title").textContent = "No path selected";
    $("missing-detail-copy").textContent = "Adjust the filters or run another scan.";
    setStatusLine("missing-detail-status", "Not selected", "muted");
    $("missing-detail-stats").replaceChildren();
    setPre("missing-preview", "No technical references to show.");
    return;
  }
  const health = missingPathHealth(row);
  $("missing-detail-title").textContent = compactPath(row.path);
  $("missing-detail-copy").textContent = `${row.label || "Workspace"} is referenced by Codex but is not available at this location.`;
  setStatusLine("missing-detail-status", health.label, health.kind);
  const stats = [
    ["Related chats", row.chat_count || 0],
    ["Session files", (row.session_files || []).length],
    ["Database rows", countSqliteRows(row.sqlite)],
    ["Index entries", (row.session_index_lines_to_remove || []).length],
  ];
  const statsRoot = $("missing-detail-stats");
  statsRoot.replaceChildren();
  stats.forEach(([label, value]) => {
    const node = el("div", "mini-stat");
    node.append(el("span", "", label), el("strong", "", value));
    statsRoot.appendChild(node);
  });
  $("missing-move-action").disabled = !row.safe_to_delete;
  $("missing-project-action").disabled = !row.safe_to_delete;
  setPre("missing-preview", row.preview || row);
}

function focusedMissingPath() {
  return state.missingPaths.find((row) => row.path === state.focusedMissingPath) || null;
}

async function previewMissingPathTrash(path) {
  const result = await api("/api/missing-paths/preview-trash", {
    method: "POST",
    body: { path },
  });
  setPre("missing-preview", result);
  openDetails("missing-technical-details");
  return result;
}

async function moveMissingPathTrash(path) {
  return runControlledAction(async () => {
    const preview = await previewMissingPathTrash(path);
    await confirmAction({
      title: "Move missing path to Trash Bin",
      message: `Selected path: ${path}. This moves related chats, session files, and Codex metadata references into Trash Bin. Type MOVE MISSING PATHS TO TRASH.`,
      phrase: "MOVE MISSING PATHS TO TRASH",
      dangerous: true,
      run: async () => {
        const result = await api("/api/missing-paths/trash", {
          method: "POST",
          body: {
            path,
            preview_token: preview.preview_token,
            confirm: true,
            confirmation: "MOVE MISSING PATHS TO TRASH",
          },
        });
        setPre("missing-preview", result);
        await Promise.all([loadMissingPaths(), loadChats(), loadTrashBin(), loadLogs()]);
        toast(`Missing path result: ${result.status}`);
      },
    });
  });
}

async function moveAllMissingPathsTrash() {
  return runControlledAction(async () => {
    const preview = await api("/api/missing-paths/preview-trash-all", { method: "POST", body: {} });
    setPre("missing-preview", preview);
    openDetails("missing-technical-details");
    await confirmAction({
      title: "Move all unrecoverable missing paths to Trash Bin",
      message: `Selected paths: ${(preview.paths || []).join(", ") || "none"}. This batch moves those unavailable paths and all related Codex chats into Trash Bin. Type MOVE MISSING PATHS TO TRASH.`,
      phrase: "MOVE MISSING PATHS TO TRASH",
      dangerous: true,
      run: async () => {
        const result = await api("/api/missing-paths/trash-all", {
          method: "POST",
          body: {
            preview_token: preview.preview_token,
            confirm: true,
            confirmation: "MOVE MISSING PATHS TO TRASH",
          },
        });
        setPre("missing-preview", result);
        await Promise.all([loadMissingPaths(), loadChats(), loadTrashBin(), loadLogs()]);
        toast(`Missing paths result: ${result.status}`);
      },
    });
  });
}

async function deleteProjectFromCodex(path) {
  return runControlledAction(async () => {
    const preview = await api("/api/projects/preview-delete", { method: "POST", body: { path } });
    setPre("missing-preview", preview);
    await confirmAction({
      title: "Delete project from Codex",
      message: `Selected project: ${path}. This removes Codex project references and all related chats into Trash Bin. It does not delete the actual project folder. Type DELETE PROJECT FROM CODEX.`,
      phrase: "DELETE PROJECT FROM CODEX",
      dangerous: true,
      run: async () => {
        const result = await api("/api/projects/delete", {
          method: "POST",
          body: {
            path,
            preview_token: preview.preview_token,
            confirm: true,
            confirmation: "DELETE PROJECT FROM CODEX",
          },
        });
        setPre("missing-preview", result);
        await Promise.all([loadMissingPaths(), loadChats(), loadTrashBin(), loadLogs()]);
        toast(`Project delete result: ${result.status}`);
      },
    });
  });
}

async function previewRelocation() {
  const body = relocationBody();
  const result = await api("/api/relocation/preview", { method: "POST", body });
  setPre("relocation-preview", result);
  state.relocationPreview = { body: { ...body }, token: result.preview_token };
  $("apply-relocation").disabled = !result.safe_to_apply;
  return result;
}

async function applyRelocation() {
  return runControlledAction(async () => {
    const body = relocationBody();
    const saved = state.relocationPreview;
    if (!saved || JSON.stringify(saved.body) !== JSON.stringify(body)) {
      throw new Error("Preview these exact paths before applying relocation");
    }
    await confirmAction({
      title: "Apply relocation",
      message: `Relocate ${body.old_path} to ${body.new_path}. This updates current Codex routing references after creating backups. Historical raw session logs are not rewritten. Type APPLY RELOCATION.`,
      phrase: "APPLY RELOCATION",
      dangerous: true,
      run: async () => {
        const result = await api("/api/relocation/apply", {
          method: "POST",
          body: {
            ...body,
            preview_token: saved.token,
            confirm: true,
            confirmation: "APPLY RELOCATION",
          },
        });
        state.relocationPreview = null;
        $("apply-relocation").disabled = true;
        setPre("relocation-preview", result);
        await Promise.all([loadOverview(), loadChats(), loadMissingPaths(), loadTrashBin(), loadLogs()]);
        toast("Relocation applied with backup.");
      },
    });
  });
}

function invalidateRelocationPreview() {
  state.relocationPreview = null;
  $("apply-relocation").disabled = true;
}

function relocationBody() {
  const oldPath = $("old-path").value.trim();
  const newPath = $("new-path").value.trim();
  if (!oldPath || !newPath) {
    throw new Error("Both old path and new path are required.");
  }
  return { old_path: oldPath, new_path: newPath };
}

async function loadTrashBin() {
  const requestId = ++state.requests.trash;
  renderListSkeleton("trash-bin-list");
  try {
    const data = await api("/api/trash-bin");
    if (requestId !== state.requests.trash) return;
    state.trashBin = data.items || [];
    if (!state.trashBin.some((item) => item.item_id === state.focusedTrashItemId)) {
      state.focusedTrashItemId = state.trashBin[0]?.item_id || null;
    }
    renderTrashBin("trash-bin-list");
    renderTrashBin("cleaner-trash-bin-list", "stale-file");
  } finally {
    if (requestId === state.requests.trash) $("trash-bin-list")?.removeAttribute("aria-busy");
  }
}

function renderTrashBin(rootId, itemType = "") {
  const root = $(rootId);
  if (!root) return;
  root.replaceChildren();
  root.removeAttribute("aria-busy");
  const items = itemType ? state.trashBin.filter((item) => item.item_type === itemType) : state.trashBin;
  if (rootId === "trash-bin-list") {
    $("trash-result-count").textContent = `${items.length} item${items.length === 1 ? "" : "s"}`;
  }
  if (items.length === 0) {
    root.appendChild(el("div", "list-item muted", itemType ? "No matching Trash Bin items." : "Trash Bin is empty."));
    if (rootId === "trash-bin-list") renderTrashInspector(null);
    return;
  }
  items.forEach((item) => {
    const node = el("div", `list-item trash-row${item.item_id === state.focusedTrashItemId ? " is-focused" : ""}`);
    const content = el("div");
    content.appendChild(el("strong", "", item.title || compactPath(item.path) || item.item_id));
    const meta = el("div", "trash-row-meta");
    meta.append(
      el("span", "", item.item_type || "item"),
      el("span", "", bytes(item.size)),
      el("span", "", formatCompactTime(item.deleted_at)),
      el("span", "", `${(item.thread_ids || []).length} chat${(item.thread_ids || []).length === 1 ? "" : "s"}`),
    );
    content.appendChild(meta);
    if (item.path) content.appendChild(el("div", "path truncate-path", compactPath(item.path)));
    const review = el("button", "row-action", rootId === "trash-bin-list" ? "Review" : "Open Trash Bin");
    review.addEventListener("click", () => {
      state.focusedTrashItemId = item.item_id;
      if (rootId !== "trash-bin-list") setPage("trashbin");
      renderTrashBin("trash-bin-list");
    });
    node.append(content, review);
    node.addEventListener("click", (event) => {
      if (event.target.closest("button")) return;
      state.focusedTrashItemId = item.item_id;
      renderTrashBin("trash-bin-list");
    });
    root.appendChild(node);
  });
  if (rootId === "trash-bin-list") {
    renderTrashInspector(items.find((item) => item.item_id === state.focusedTrashItemId) || items[0]);
  }
}

function renderTrashInspector(item) {
  const enabled = Boolean(item);
  ["trash-preview-restore", "trash-restore", "trash-permanent-delete"].forEach((id) => {
    $(id).disabled = !enabled;
  });
  if (!item) {
    $("trash-detail-title").textContent = "Trash Bin is empty";
    $("trash-detail-id").textContent = "Deleted items will appear here with their restore data.";
    setStatusLine("trash-detail-status", "Empty", "muted");
    renderDetailList("trash-detail-list", []);
    setPre("trash-bin-preview", "No restore data to show.");
    return;
  }
  $("trash-detail-title").textContent = item.title || compactPath(item.path) || "Trash item";
  $("trash-detail-id").textContent = item.item_id;
  const status = item.status === "trashed" ? { label: "Restorable", kind: "ok" } : { label: text(item.status), kind: "warn" };
  setStatusLine("trash-detail-status", status.label, status.kind);
  renderDetailList("trash-detail-list", [
    ["Type", item.item_type || "-"],
    ["Deleted", formatCompactTime(item.deleted_at)],
    ["Size", bytes(item.size)],
    ["Related chats", (item.thread_ids || []).length],
    ["Original path", item.path ? compactPath(item.path) : "-", "path"],
  ]);
  setPre("trash-bin-preview", item);
}

function focusedTrashItem() {
  return state.trashBin.find((item) => item.item_id === state.focusedTrashItemId) || null;
}

async function previewTrashBinRestore(itemId) {
  const result = await api("/api/trash-bin/preview-restore", {
    method: "POST",
    body: { item_id: itemId },
  });
  setPre("trash-bin-preview", result);
  openDetails("trash-technical-details");
  if ($("cleaner-preview")) setPre("cleaner-preview", result);
  return result;
}

async function restoreTrashBinItem(itemId) {
  return runControlledAction(async () => {
    const preview = await previewTrashBinRestore(itemId);
    await confirmAction({
      title: "Restore from Trash Bin",
      message: `Selected Trash Bin item: ${itemId}. This restores moved files and proven metadata references. Type RESTORE FROM TRASH.`,
      phrase: "RESTORE FROM TRASH",
      run: async () => {
        const result = await api("/api/trash-bin/restore", {
          method: "POST",
          body: {
            item_id: itemId,
            preview_token: preview.preview_token,
            confirm: true,
            confirmation: "RESTORE FROM TRASH",
          },
        });
        setPre("trash-bin-preview", result);
        if ($("cleaner-preview")) setPre("cleaner-preview", result);
        await Promise.all([loadChats(), loadMissingPaths(), loadTrashBin(), loadLogs()]);
        toast(`Restore result: ${result.status}`);
      },
    });
  });
}

async function permanentlyDeleteTrashBinItem(itemId) {
  return runControlledAction(async () => {
    const preview = await api("/api/trash-bin/preview-permanent-delete", {
      method: "POST",
      body: { item_id: itemId },
    });
    setPre("trash-bin-preview", preview);
    await confirmAction({
      title: "Permanently delete Trash Bin item",
      message: `Selected Trash Bin item: ${itemId}. This deletes only that Trash Bin item folder, never active .codex files. Type PERMANENT DELETE.`,
      phrase: "PERMANENT DELETE",
      dangerous: true,
      run: async () => {
        const result = await api("/api/trash-bin/permanent-delete", {
          method: "POST",
          body: {
            item_id: itemId,
            preview_token: preview.preview_token,
            confirm: true,
            confirmation: "PERMANENT DELETE",
          },
        });
        setPre("trash-bin-preview", result);
        if ($("cleaner-preview")) setPre("cleaner-preview", result);
        await Promise.all([loadTrashBin(), loadLogs()]);
        toast("Trash Bin item permanently deleted.");
      },
    });
  });
}

async function scanFolderCleaner() {
  const requestId = ++state.requests.cleaner;
  renderCleanerSkeleton();
  beginScan("cleaner-scan", "Scanning");
  try {
    const [data] = await Promise.all([
      api("/api/folder-cleaner/scan"),
      pause(240),
    ]);
    if (requestId !== state.requests.cleaner) return;
    state.cleanerItems = data.items || [];
    state.cleanerSummary = data.summary || null;
    state.cleanerSelected.clear();
    if (!state.cleanerItems.some((item) => item.path === state.focusedCleanerPath)) {
      state.focusedCleanerPath = state.cleanerItems[0]?.path || null;
    }
    renderCleanerFilters();
    renderCleanerSummary();
    renderCleanerRows();
    setPre("cleaner-preview", {
      cleanliness_score: state.cleanerSummary?.cleanliness_score,
      total_files: state.cleanerSummary?.total_files,
      stale_candidates: state.cleanerSummary?.stale_candidates,
      archive_candidate_size: state.cleanerSummary?.archive_candidate_size,
    });
  } finally {
    endScan("cleaner-scan", requestId !== state.requests.cleaner);
  }
}

function renderCleanerFilters() {
  const typeSelect = $("cleaner-type");
  const current = typeSelect.value;
  const types = [...new Set(state.cleanerItems.map((item) => item.file_type).filter(Boolean))].sort();
  typeSelect.replaceChildren(new Option("All types", ""));
  types.forEach((type) => typeSelect.appendChild(new Option(type, type)));
  typeSelect.value = types.includes(current) ? current : "";
}

function renderCleanerSummary() {
  const summary = state.cleanerSummary || {};
  $("cleaner-score").textContent = summary.cleanliness_score || "-";
  $("cleaner-files").textContent = summary.total_files ?? "-";
  $("cleaner-stale").textContent = summary.stale_candidates ?? "-";
  $("cleaner-unknown").textContent = summary.unknown_files ?? "-";
}

function filteredCleanerItems() {
  const search = $("cleaner-search").value.trim().toLowerCase();
  const risk = $("cleaner-risk").value;
  const type = $("cleaner-type").value;
  return state.cleanerItems.filter((item) => {
    if (risk && item.risk_level !== risk) return false;
    if (type && item.file_type !== type) return false;
    if (!search) return true;
    return [item.name, item.path, item.relative_path, item.explanation, item.file_type, item.risk_level]
      .some((value) => String(value || "").toLowerCase().includes(search));
  });
}

function renderCleanerRows() {
  const body = $("cleaner-rows");
  const rows = filteredCleanerItems();
  const visibleRows = rows.slice(0, 1000);
  body.replaceChildren();
  body.removeAttribute("aria-busy");
  if (visibleRows.length === 0) {
    const row = document.createElement("tr");
    const cell = el("td", "muted", state.cleanerItems.length ? "No files match the current filters." : "Run a scan to inspect the Codex data folder.");
    cell.colSpan = 6;
    row.appendChild(cell);
    body.appendChild(row);
    renderCleanerInspector(null);
    updateCleanerSelectionState([]);
    return;
  }
  visibleRows.forEach((item) => {
    const row = document.createElement("tr");
    row.dataset.cleanerRow = item.path;
    row.classList.toggle("is-focused", item.path === state.focusedCleanerPath);
    const checkCell = el("td", "check-col");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.dataset.cleanerPath = item.path;
    checkbox.checked = state.cleanerSelected.has(item.path);
    checkbox.disabled = !item.safe_to_archive;
    checkbox.setAttribute("aria-label", `Select ${item.name}`);
    checkCell.appendChild(checkbox);

    const fileCell = el("td", "title-cell");
    fileCell.appendChild(el("strong", "", item.name));
    fileCell.appendChild(el("div", "path truncate-path", compactPath(item.relative_path)));

    const classificationCell = el("td");
    const riskKind = cleanerRiskStatus(item.risk_level);
    classificationCell.appendChild(statusNode(riskKind.label, riskKind.kind));
    classificationCell.appendChild(el("div", "muted", item.safe_to_archive ? "Archive candidate" : "Inspection only"));

    const sizeCell = el("td", "", item.is_directory ? "-" : bytes(item.size));
    const modifiedCell = el("td", "muted", formatCompactTime(item.modified_at));

    const actionCell = el("td");
    const review = el("button", "row-action", "Review");
    review.addEventListener("click", (event) => {
      event.stopPropagation();
      state.focusedCleanerPath = item.path;
      renderCleanerRows();
    });
    actionCell.appendChild(review);

    row.addEventListener("click", (event) => {
      if (event.target.closest("input, button")) return;
      state.focusedCleanerPath = item.path;
      renderCleanerRows();
    });

    row.append(checkCell, fileCell, classificationCell, sizeCell, modifiedCell, actionCell);
    body.appendChild(row);
  });
  if (rows.length > visibleRows.length) {
    const row = document.createElement("tr");
    const cell = el("td", "muted", `Showing first ${visibleRows.length} of ${rows.length} matches. Use search or filters to narrow results.`);
    cell.colSpan = 6;
    row.appendChild(cell);
    body.appendChild(row);
  }
  renderCleanerInspector(visibleRows.find((item) => item.path === state.focusedCleanerPath) || visibleRows[0]);
  updateCleanerSelectionState(visibleRows);
}

function cleanerRiskStatus(risk) {
  if (risk === "active" || risk === "important") return { label: risk === "active" ? "Active" : "Important", kind: "ok" };
  if (risk === "backup" || risk === "stale") return { label: risk === "backup" ? "Backup" : "Stale", kind: "warn" };
  if (risk === "danger") return { label: "Do not touch", kind: "bad" };
  return { label: "Unknown", kind: "info" };
}

function renderCleanerInspector(item) {
  if (!item) {
    $("cleaner-detail-title").textContent = "No file selected";
    $("cleaner-detail-path").textContent = "Adjust the filters or run another scan.";
    setStatusLine("cleaner-detail-status", "Not selected", "muted");
    renderDetailList("cleaner-detail-list", []);
    $("cleaner-detail-explanation").hidden = true;
    setPre("cleaner-preview", "No technical details to show.");
    return;
  }
  const risk = cleanerRiskStatus(item.risk_level);
  $("cleaner-detail-title").textContent = item.name;
  $("cleaner-detail-path").textContent = compactPath(item.path);
  setStatusLine("cleaner-detail-status", risk.label, risk.kind);
  renderDetailList("cleaner-detail-list", [
    ["Type", item.file_type || "-"],
    ["Size", item.is_directory ? "Directory" : bytes(item.size)],
    ["Modified", formatCompactTime(item.modified_at)],
    ["Last accessed", formatCompactTime(item.accessed_at)],
    ["Codex use", item.likely_active_codex_data ? "Likely active" : "No active use detected"],
    ["Safe action", item.safe_to_archive ? "Move to Trash Bin after preview" : "Leave in place"],
  ]);
  const explanation = $("cleaner-detail-explanation");
  explanation.hidden = false;
  explanation.replaceChildren(el("strong", "", "Why this classification"), document.createTextNode(item.explanation || "No additional explanation is available."));
  setPre("cleaner-preview", item);
}

function riskBadgeKind(risk) {
  if (risk === "active" || risk === "important") return "ok";
  if (risk === "backup" || risk === "stale") return "warn";
  if (risk === "danger") return "bad";
  return "";
}

function updateCleanerSelectionState(visibleRows = filteredCleanerItems()) {
  const count = state.cleanerSelected.size;
  $("cleaner-selection-count").textContent = count ? `${count} selected` : "Select safe files to manage";
  $("preview-archive").disabled = count === 0;
  $("apply-archive").disabled = count === 0;
  const selectable = visibleRows.filter((item) => item.safe_to_archive);
  $("select-all-cleaner").checked = selectable.length > 0 && selectable.every((item) => state.cleanerSelected.has(item.path));
  $("select-all-cleaner").indeterminate = selectable.some((item) => state.cleanerSelected.has(item.path)) && !$("select-all-cleaner").checked;
}

async function previewCleanerArchive(paths = [...state.cleanerSelected]) {
  const result = await api("/api/folder-cleaner/preview-archive", {
    method: "POST",
    body: { paths },
  });
  setPre("cleaner-preview", result);
  openDetails("cleaner-technical-details");
  return result;
}

async function applyCleanerArchive() {
  return runControlledAction(async () => {
    const paths = [...state.cleanerSelected];
    const preview = await previewCleanerArchive(paths);
    await confirmAction({
      title: "Move selected Codex files to Trash Bin",
      message: `Selected files: ${paths.map((path) => path.split("/").pop()).join(", ")}. This moves reviewed stale/backup candidates into Trash Bin while preserving their paths. Type MOVE CODEX FILES TO TRASH.`,
      phrase: "MOVE CODEX FILES TO TRASH",
      dangerous: true,
      run: async () => {
        const result = await api("/api/folder-cleaner/archive", {
          method: "POST",
          body: {
            paths,
            preview_token: preview.preview_token,
            confirm: true,
            confirmation: "MOVE CODEX FILES TO TRASH",
          },
        });
        setPre("cleaner-preview", result);
        state.cleanerSelected.clear();
        await Promise.all([scanFolderCleaner(), loadTrashBin(), loadLogs()]);
        toast(`Move result: ${result.status}`);
      },
    });
  });
}

async function generateCleanerReport() {
  const result = await api("/api/folder-cleaner/report", { method: "POST", body: {} });
  setPre("cleaner-preview", result);
  toast("Cleanliness report generated.");
}

async function loadLogs() {
  const requestId = ++state.requests.logs;
  renderListSkeleton("operation-log-list");
  try {
    const data = await api("/api/logs?limit=250");
    if (requestId !== state.requests.logs) return;
    state.logs = data.logs || [];
    if (state.focusedLogIndex === null || state.focusedLogIndex >= state.logs.length) {
      state.focusedLogIndex = state.logs.length ? 0 : null;
    }
    renderLogs();
  } finally {
    if (requestId === state.requests.logs) $("operation-log-list")?.removeAttribute("aria-busy");
  }
}

function renderLogs() {
  const root = $("operation-log-list");
  if (!root) return;
  root.replaceChildren();
  root.removeAttribute("aria-busy");
  $("log-result-count").textContent = `${state.logs.length} event${state.logs.length === 1 ? "" : "s"}`;
  if (state.logs.length === 0) {
    root.appendChild(el("div", "list-item muted", "No operation logs yet."));
    renderLogInspector(null);
    return;
  }
  state.logs.forEach((log, index) => {
    const row = el("div", `list-item log-row${index === state.focusedLogIndex ? " is-focused" : ""}`);
    const body = el("div");
    body.appendChild(el("strong", "", operationLabel(log)));
    const isDryRun = Boolean(log.dry_run);
    body.appendChild(statusNode(isDryRun ? "Preview" : text(log.status || log.result?.status || "Recorded"), isDryRun ? "info" : "ok"));
    body.appendChild(el("div", "muted", formatCompactTime(log.logged_at || log.timestamp || log.created_at)));
    const review = el("button", "row-action", "Review");
    review.addEventListener("click", () => {
      state.focusedLogIndex = index;
      renderLogs();
    });
    row.append(body, review);
    row.addEventListener("click", (event) => {
      if (event.target.closest("button")) return;
      state.focusedLogIndex = index;
      renderLogs();
    });
    root.appendChild(row);
  });
  renderLogInspector(state.logs[state.focusedLogIndex] || state.logs[0]);
}

function operationLabel(log) {
  return text(log.operation_type || log.operation || "Operation")
    .replace(/[-_]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function renderLogInspector(log) {
  if (!log) {
    $("log-detail-title").textContent = "No operation selected";
    $("log-detail-time").textContent = "Applied operations and previews are recorded here.";
    setStatusLine("log-detail-status", "Empty", "muted");
    renderDetailList("log-detail-list", []);
    setPre("operation-logs", "No operation logs yet.");
    return;
  }
  const isDryRun = Boolean(log.dry_run);
  $("log-detail-title").textContent = operationLabel(log);
  $("log-detail-time").textContent = formatCompactTime(log.logged_at || log.timestamp || log.created_at);
  setStatusLine("log-detail-status", isDryRun ? "Preview" : text(log.status || "Recorded"), isDryRun ? "info" : "ok");
  renderDetailList("log-detail-list", [
    ["Mode", isDryRun ? "Dry run" : "Applied"],
    ["Result", text(log.status || log.result?.status || log.result?.cleanliness_score || "Recorded")],
    ["Files affected", (log.files_affected || log.files_modified || []).length],
    ["Thread", log.thread_id || (log.thread_ids || []).join(", ") || "-", "mono"],
    ["Backup", compactPath(log.backup_folder_path || log.backup_dir || "-") , "path"],
  ]);
  setPre("operation-logs", log);
}

function confirmAction({ title, message, phrase, run, dangerous = false }) {
  const dialog = $("confirm-dialog");
  if (dialog.open) {
    return Promise.reject(new Error("Another confirmation is already open"));
  }
  dialog.returnValue = "";
  $("confirm-title").textContent = title;
  $("confirm-message").textContent = message;
  $("confirm-input").value = "";
  $("confirm-input").placeholder = phrase;
  $("confirm-submit").disabled = true;
  $("confirm-submit").className = dangerous ? "danger-solid" : "primary";

  return new Promise((resolve) => {
    const input = $("confirm-input");
    const submit = $("confirm-submit");
    const onInput = () => {
      submit.disabled = input.value !== phrase;
    };
    const onCancel = () => {
      dialog.returnValue = "cancel";
    };
    const onClose = async () => {
      input.removeEventListener("input", onInput);
      dialog.removeEventListener("close", onClose);
      dialog.removeEventListener("cancel", onCancel);
      if (dialog.returnValue !== "confirm" || input.value !== phrase) {
        resolve();
        return;
      }
      try {
        await run();
      } catch (error) {
        toast(error.message);
      }
      resolve();
    };
    input.addEventListener("input", onInput);
    dialog.addEventListener("cancel", onCancel);
    dialog.addEventListener("close", onClose, { once: true });
    dialog.showModal();
    input.focus();
  });
}

function bindEvents() {
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => {
      playNavMotion(button);
      setPage(button.dataset.page);
    });
  });
  $("refresh-all").addEventListener("click", () => refreshAll().catch((error) => toast(error.message)));
  $("chat-refresh").addEventListener("click", () => loadChats().catch((error) => toast(error.message)));
  $("chat-search").addEventListener("input", debounce(() => loadChats().catch((error) => toast(error.message)), 180));
  $("chat-archived").addEventListener("change", () => loadChats().catch((error) => toast(error.message)));
  $("chat-cwd").addEventListener("change", () => loadChats().catch((error) => toast(error.message)));
  $("preview-trash").addEventListener("click", () => previewTrash().catch((error) => toast(error.message)));
  $("apply-trash").addEventListener("click", () => applyTrash().catch((error) => toast(error.message)));
  $("missing-scan").addEventListener("click", () => loadMissingPaths().catch((error) => toast(error.message)));
  $("missing-trash-all").addEventListener("click", () => moveAllMissingPathsTrash().catch((error) => toast(error.message)));
  $("missing-search").addEventListener("input", debounce(() => renderMissingPathCleaner(), 120));
  $("missing-status").addEventListener("change", () => renderMissingPathCleaner());
  $("missing-relink").addEventListener("click", () => {
    const row = focusedMissingPath();
    if (!row) return;
    $("old-path").value = row.path;
    invalidateRelocationPreview();
    setPage("relocation");
    toast("Missing path added to the relocation form.");
  });
  $("missing-preview-action").addEventListener("click", () => {
    const row = focusedMissingPath();
    if (row) previewMissingPathTrash(row.path).catch((error) => toast(error.message));
  });
  $("missing-move-action").addEventListener("click", () => {
    const row = focusedMissingPath();
    if (row) moveMissingPathTrash(row.path).catch((error) => toast(error.message));
  });
  $("missing-project-action").addEventListener("click", () => {
    const row = focusedMissingPath();
    if (row) deleteProjectFromCodex(row.path).catch((error) => toast(error.message));
  });
  $("preview-relocation").addEventListener("click", () => previewRelocation().catch((error) => toast(error.message)));
  $("apply-relocation").addEventListener("click", () => applyRelocation().catch((error) => toast(error.message)));
  $("old-path").addEventListener("input", invalidateRelocationPreview);
  $("new-path").addEventListener("input", invalidateRelocationPreview);
  $("trash-preview-restore").addEventListener("click", () => {
    const item = focusedTrashItem();
    if (item) previewTrashBinRestore(item.item_id).catch((error) => toast(error.message));
  });
  $("trash-restore").addEventListener("click", () => {
    const item = focusedTrashItem();
    if (item) restoreTrashBinItem(item.item_id).catch((error) => toast(error.message));
  });
  $("trash-permanent-delete").addEventListener("click", () => {
    const item = focusedTrashItem();
    if (item) permanentlyDeleteTrashBinItem(item.item_id).catch((error) => toast(error.message));
  });
  $("cleaner-scan").addEventListener("click", () => scanFolderCleaner().catch((error) => toast(error.message)));
  $("cleaner-report").addEventListener("click", () => generateCleanerReport().catch((error) => toast(error.message)));
  $("cleaner-search").addEventListener("input", debounce(() => renderCleanerRows(), 120));
  $("cleaner-risk").addEventListener("change", () => renderCleanerRows());
  $("cleaner-type").addEventListener("change", () => renderCleanerRows());
  $("preview-archive").addEventListener("click", () => previewCleanerArchive().catch((error) => toast(error.message)));
  $("apply-archive").addEventListener("click", () => applyCleanerArchive().catch((error) => toast(error.message)));
  $("select-all-cleaner").addEventListener("change", (event) => {
    const rows = filteredCleanerItems().filter((item) => item.safe_to_archive);
    if (event.target.checked) {
      rows.forEach((item) => state.cleanerSelected.add(item.path));
    } else {
      rows.forEach((item) => state.cleanerSelected.delete(item.path));
    }
    renderCleanerRows();
  });
  $("cleaner-rows").addEventListener("change", (event) => {
    if (!event.target.dataset.cleanerPath) return;
    if (event.target.checked) {
      state.cleanerSelected.add(event.target.dataset.cleanerPath);
    } else {
      state.cleanerSelected.delete(event.target.dataset.cleanerPath);
    }
    updateCleanerSelectionState();
  });
  $("select-all-chats").addEventListener("change", (event) => {
    if (event.target.checked) {
      state.chats.forEach((chat) => state.selectedChats.add(chat.id));
    } else {
      state.selectedChats.clear();
    }
    renderChats();
  });
  $("chat-rows").addEventListener("change", (event) => {
    if (!event.target.dataset.threadId) return;
    if (event.target.checked) {
      state.selectedChats.add(event.target.dataset.threadId);
    } else {
      state.selectedChats.delete(event.target.dataset.threadId);
    }
    updateSelectionState();
  });
}

function debounce(fn, wait) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

async function refreshAll() {
  let healthOk = false;
  renderChatSkeleton();
  renderMissingSkeleton();
  renderListSkeleton("trash-bin-list");
  renderListSkeleton("operation-log-list");
  beginScan("refresh-all", "Scanning");
  try {
    healthOk = await loadHealth({ updateStatus: false });
    await loadOverview();
    await loadPaths();
    await loadChats();
    await loadMissingPaths();
    await loadTrashBin();
    await loadLogs();
  } finally {
    endScan("refresh-all");
    setConnectionState(healthOk ? "connected" : "unavailable");
  }
}

consumeSessionToken();
bindEvents();
refreshAll().catch((error) => toast(error.message));
