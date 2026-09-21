// Minimal safe DOM construction helpers. All dynamic text is assigned via
// textContent so API-supplied data can never become markup.
export function h(tag, attrs, ...children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "dataset") Object.assign(node.dataset, value);
      else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
      else if (key === "value") node.value = value;
      else if (key === "checked") node.checked = Boolean(value);
      else if (key === "disabled") node.disabled = Boolean(value);
      else node.setAttribute(key, String(value));
    }
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function timeAgo(iso) {
  if (!iso) return "-";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return iso;
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return new Date(then).toLocaleString();
}

export function fullTime(iso) {
  if (!iso) return "-";
  const parsed = Date.parse(iso);
  return Number.isNaN(parsed) ? iso : new Date(parsed).toLocaleString();
}

export function shortHash(hash, head = 10, tail = 6) {
  if (!hash || hash.length <= head + tail + 1) return hash || "-";
  return `${hash.slice(0, head)}…${hash.slice(-tail)}`;
}

export function badge(text, tone) {
  return h("span", { class: `badge badge-${tone}` }, text);
}

const STATUS_TONES = {
  queued: "muted", running: "info", done: "ok", failed: "bad",
  cancel_requested: "warn", cancelled: "warn",
  success: "ok", error: "bad",
};

export function statusBadge(status) {
  return badge(status || "unknown", STATUS_TONES[status] || "muted");
}

export function jsonBlock(value) {
  let text;
  try { text = JSON.stringify(value, null, 2); } catch { text = String(value); }
  return h("pre", { class: "json-block" }, text);
}

export function missingNote(what, why) {
  return h("div", { class: "missing" },
    h("span", { class: "missing-tag" }, "Missing"),
    h("span", null, ` ${what}. ${why}`));
}

export function toast(message, tone = "info", ms = 5000) {
  const host = document.getElementById("toasts");
  if (!host) return;
  const item = h("div", { class: `toast toast-${tone}`, role: "status" }, message);
  host.append(item);
  setTimeout(() => item.remove(), ms);
}
