// The gcpe capability browser. Renders /docs/_/catalogue; "Call API" fetches the
// example's own /v1 URL, exactly as any API client would. Every server value
// reaches the page through textContent, never parsed as markup.
"use strict";

const state = { session: null, catalogue: null, scope: null };
const SCOPE_KEY = "gcpe-docs-scope";
const MAX_ROWS = 100;
// Response fields that qualify a result. Shown verbatim whenever present: an
// audit answer read without its caveats is easily over-trusted.
const NOTE_FIELDS = [
  ["query", "CAI query"],
  ["queries", "CAI queries"],
  ["suppressed_summary", "Hidden"],
  ["iam_note", "IAM"],
  ["coverage_note", "Coverage"],
  ["unclassified_roles", "Unclassified roles"],
  ["accounts", "Billing accounts read"],
];
const PREFERRED_COLUMNS = [
  "key", "display_name", "name", "title", "member", "role", "risk", "highest_risk",
  "asset_type", "project_id", "location", "resource", "resource_count", "custom",
];

// Children may be nested arrays to any depth, and null/false/"" mean "nothing".
// Everything that puts nodes on the page goes through this, because a DOM
// method handed an array stringifies it ("a,b" or "[object HTMLElement]").
function nodes(children) {
  return children
    .flat(Infinity)
    .filter((c) => c !== null && c !== undefined && c !== false && c !== "")
    .map((c) => (c instanceof Node ? c : document.createTextNode(String(c))));
}

function put(parent, ...children) {
  parent.replaceChildren(...nodes(children));
}

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  node.append(...nodes(children));
  return node;
}

class ApiError extends Error {
  constructor(status, body) {
    super(body && body.detail ? body.detail : `HTTP ${status}`);
    this.status = status;
    this.body = body;
  }
}

async function getJSON(url) {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, body);
  return body;
}

function storage(action, value) {
  try {
    if (action === "get") return localStorage.getItem(SCOPE_KEY);
    localStorage.setItem(SCOPE_KEY, value);
  } catch {
    return null;
  }
  return null;
}

// --- Session and scope ---------------------------------------------------------

function renderSession(session) {
  const box = document.getElementById("session");
  const banner = document.getElementById("banner");
  if (session.error) {
    put(box, "Not signed in");
    banner.hidden = false;
    put(banner,
      "No Application Default Credentials. Run ",
      el("code", { text: "gcloud auth application-default login" }),
      " and restart ",
      el("code", { text: "gcpe docs" }),
      ". Examples below show a placeholder scope. ",
      el("span", { class: "muted", text: `(${session.error})` }),
    );
    return;
  }
  const who = session.principal
    ? el("strong", { text: session.principal })
    : el("strong", { text: "unknown principal", title: session.principal_note || "" });
  // Where calls bill is in the banner below; the header stays one line.
  put(box, "Signed in as ", who, ` (${session.credential_type || "unknown credential"})`);
  banner.hidden = false;
  put(banner,
    "Call API makes a real, read-only call to Google Cloud as you",
    session.quota_project ? `, billed to ${session.quota_project}.` : ".",
  );
}

async function loadProjects() {
  const status = document.getElementById("scope-status");
  status.textContent = "Listing your projects…";
  try {
    const { projects } = await getJSON("/docs/_/projects");
    const list = document.getElementById("projects");
    put(list,
      ...projects.map((p) =>
        el("option", { value: `projects/${p.project_id}`, label: p.display_name || p.project_id })),
    );
    status.textContent = `${projects.length} project(s) visible. Folders and organizations can be typed.`;
  } catch (error) {
    // The reason is the useful part (usually "enable this API, here's how"),
    // so it is shown, not hidden in a tooltip.
    put(status, el("details", { class: "picker-error" },
      el("summary", { text: "Project list unavailable; type a scope instead. Why?" }),
      el("div", { class: "error", text: error.message })));
  }
}

async function loadCatalogue(scope) {
  const status = document.getElementById("scope-status");
  try {
    const catalogue = await getJSON(`/docs/_/catalogue?scope=${encodeURIComponent(scope)}`);
    state.catalogue = catalogue;
    state.scope = catalogue.scope;
    document.getElementById("scope").value = catalogue.scope;
    storage("set", catalogue.scope);
    renderNav();
    route();
    return true;
  } catch (error) {
    status.textContent = `Scope not applied: ${error.message}`;
    return false;
  }
}

// --- Navigation ------------------------------------------------------------------

function currentId() {
  return decodeURIComponent(location.hash.replace(/^#\/?/, ""));
}

function renderNav() {
  const nav = document.getElementById("nav");
  const groups = state.catalogue.themes.map((theme) => {
    const caps = state.catalogue.capabilities.filter((c) => c.theme === theme);
    return [
      el("h2", { text: theme }),
      caps.map((c) => el("a", { href: `#/${c.id}`, "data-id": c.id, text: c.title })),
    ];
  });
  put(nav, el("a", { href: "#/", "data-id": "", text: "Overview" }), groups);
}

function route() {
  if (!state.catalogue) return;
  const id = currentId();
  for (const link of document.querySelectorAll("nav a")) {
    if (link.dataset.id === id) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  const capability = state.catalogue.capabilities.find((c) => c.id === id);
  const main = document.getElementById("main");
  put(main, capability ? capabilityPage(capability) : overview());
  window.scrollTo(0, 0);
}

function overview() {
  return [
    el("h1", { text: "What gcpe can do" }),
    el("p", { class: "purpose" },
      "Every capability, with examples written for ", el("code", { text: state.scope }),
      ". Each one is the same call three ways: the gcpe CLI, this server's API, and gcloud."),
    ...state.catalogue.themes.map((theme) => [
      el("h2", { text: theme }),
      el("div", { class: "cards" },
        state.catalogue.capabilities.filter((c) => c.theme === theme).map((c) =>
          el("a", { class: "card", href: `#/${c.id}` },
            el("h3", { text: c.title }),
            el("p", { text: c.purpose }),
            el("div", { class: "meta" },
              c.cli_command ? el("code", { text: `gcpe ${c.cli_command}` }) : null,
              el("code", { text: `GET ${c.route}` }))))),
    ]).flat(),
  ];
}

// --- A capability ----------------------------------------------------------------

function capabilityPage(c) {
  return [
    el("h1", { text: c.title }),
    el("p", { class: "purpose", text: c.purpose }),
    el("div", { class: "meta" },
      c.cli_command ? el("code", { text: `gcpe ${c.cli_command}` }) : el("span", { class: "muted", text: "No CLI form" }),
      c.cli_aliases.length ? el("span", { class: "muted" }, "also ", c.cli_aliases.map((a) => el("code", { text: `gcpe ${a}` }))) : null,
      el("code", { text: `GET ${c.route}` }),
      el("a", { href: c.swagger, target: "_blank", rel: "noopener", text: "Swagger" }),
      c.docs.map((d) => el("a", { href: d.url, target: "_blank", rel: "noopener", text: d.label }))),
    el("h2", { text: "Examples" }),
    c.examples.map((ex) => exampleCard(c, ex)),
    el("h2", { text: "Parameters" }),
    c.params.length ? paramsTable(c) : el("p", { class: "muted", text: "None." }),
  ];
}

function paramsTable(c) {
  return el("div", { class: "table-wrap" },
    el("table", {},
      el("thead", {}, el("tr", {}, el("th", { text: "API" }), el("th", { text: "CLI" }), el("th", { text: "Description" }))),
      el("tbody", {}, c.params.map((p) =>
        el("tr", {},
          el("td", {}, el("code", { text: p.api }), p.repeatable ? el("span", { class: "badge", text: "repeatable" }) : null),
          el("td", {}, p.cli ? el("code", { text: p.cli }) : el("span", { class: "muted", text: "—" })),
          el("td", { text: p.help }))))));
}

function exampleCard(c, ex) {
  const origin = window.location.origin;
  const forms = [
    ["CLI", ex.cli, c.cli_command ? null : "No CLI form: use the API, or gcpe search -o json."],
    ["API", origin + ex.url, null],
    ["gcloud", ex.gcloud, ex.unavailable ? ex.unavailable : "No gcloud equivalent: this is gcpe's own data."],
  ];
  const body = el("div", { class: "form" });
  const tabs = el("div", { class: "tabs", role: "tablist" });
  const result = el("div", { class: "result", "aria-live": "polite" });

  const show = (index) => {
    const [, text, absent] = forms[index];
    tabs.querySelectorAll("button").forEach((b, i) => b.setAttribute("aria-selected", String(i === index)));
    put(body, text
      ? [el("pre", {}, el("code", { text })), copyButton(text)]
      : el("p", { text: absent }));
  };
  forms.forEach(([label], i) => tabs.append(el("button", { type: "button", role: "tab", onclick: () => show(i), text: label })));
  show(c.cli_command ? 0 : 1);

  return el("section", { class: "example" },
    el("header", {},
      el("h3", { text: ex.title }),
      ex.unavailable ? el("span", { class: "muted", text: ex.unavailable }) : null,
      // Always the API, whichever tab is showing: the CLI and gcloud forms
      // are for copying, and agree with it (checked by the tests, and live).
      el("button", {
        type: "button", class: "primary", disabled: Boolean(ex.unavailable),
        title: `GET ${ex.url}`,
        onclick: (event) => callApi(c, ex, result, event.currentTarget), text: "Call API",
      })),
    tabs, body, result);
}

function copyButton(text, target) {
  const button = el("button", { type: "button", class: "copy", text: "Copy" });
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = "Copied";
    } catch {
      const range = document.createRange();
      range.selectNodeContents(target || button.previousElementSibling);
      getSelection().removeAllRanges();
      getSelection().addRange(range);
      button.textContent = "Selected";
    }
    setTimeout(() => { button.textContent = "Copy"; }, 1500);
  });
  return button;
}

// --- Call API ----------------------------------------------------------------------

async function callApi(c, ex, panel, button) {
  button.disabled = true;
  put(panel, el("p", { class: "muted", text: "Running…" }));
  const started = performance.now();
  try {
    const response = await fetch(ex.url);
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      put(panel, errorBox(response.status, body));
    } else if (ex.streams) {
      await renderStream(response, panel, started, ex.url, c);
    } else {
      renderResult(await response.json(), panel, started, ex.url, c);
    }
  } catch (error) {
    put(panel, el("div", { class: "error", text: `Request failed: ${error.message}` }));
  } finally {
    button.disabled = false;
  }
}

function errorBox(status, body) {
  const detail = body && body.detail
    ? (typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail, null, 2))
    : "";
  return el("div", { class: "error" }, `${status}${body && body.error ? ` ${body.error}` : ""}\n${detail}`);
}

function seconds(started) {
  return `${((performance.now() - started) / 1000).toFixed(1)} s`;
}

function renderResult(body, panel, started, url, c) {
  const parts = [];
  if (body.truncated) {
    parts.push(el("div", { class: "warn", text: "Truncated: more results exist than were returned. Raise limit to see them." }));
  }
  const notes = NOTE_FIELDS.filter(([key]) => {
    const v = body[key];
    return Array.isArray(v) ? v.length : Boolean(v);
  });
  if (notes.length) {
    parts.push(el("dl", { class: "notes" }, notes.map(([key, label]) => [
      el("dt", { text: label }),
      el("dd", {}, Array.isArray(body[key])
        ? body[key].map((v) => el("div", {}, el("code", { text: v })))
        : key === "query" ? el("code", { text: body[key] }) : body[key]),
    ])));
  }

  const rows = rowsOf(body);
  if (rows) {
    parts.push(el("p", { class: "muted", text: `${rows.length} row(s) in ${seconds(started)}` }));
    parts.push(rows.length ? table(rows) : emptyState(c, body));
  } else if ("by_asset_type" in body) {
    parts.push(el("p", { class: "muted", text: `${body.total} resource(s) in ${seconds(started)}` }));
    if (!body.total) parts.push(emptyState(c, body));
    for (const [key, label] of [["by_asset_type", "By type"], ["by_project", "By project"], ["by_location", "By location"]]) {
      const counts = Object.entries(body[key] || {});
      if (!counts.length) continue;
      parts.push(el("h4", { text: label }), table(counts.map(([k, n]) => ({ key: k, count: n })), ["key", "count"]));
    }
  } else {
    // A plain mapping, e.g. /v1/types.
    parts.push(table(Object.entries(body).map(([k, v]) => ({ key: k, value: v })), ["key", "value"]));
  }
  parts.push(rawBlock("Raw JSON", JSON.stringify(body, null, 2), url));
  put(panel, parts);
}

function rowsOf(body) {
  if (Array.isArray(body.groups)) return body.groups;
  for (const key of ["data", "grants"]) if (Array.isArray(body[key])) return body[key];
  return null;
}

async function renderStream(response, panel, started, url, c) {
  const rows = [];
  const status = el("p", { class: "muted", text: "Streaming…" });
  const holder = el("div");
  put(panel, status, holder);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffered = "";
  for (;;) {
    const { done, value } = await reader.read();
    buffered += decoder.decode(value || new Uint8Array(), { stream: !done });
    const lines = buffered.split("\n");
    buffered = done ? "" : lines.pop();
    for (const line of lines) if (line.trim()) rows.push(JSON.parse(line));
    status.textContent = `${rows.length} row(s) so far, ${seconds(started)}`;
    if (done) break;
  }
  status.textContent = `${rows.length} row(s) streamed in ${seconds(started)}`;
  put(holder, rows.length ? table(rows) : emptyState(c, {}),
    rawBlock("Raw NDJSON", rows.map((r) => JSON.stringify(r)).join("\n"), url));
}

// An empty answer is correct but a dead end; say what was asked and where to
// go next. Resource capabilities point at the summary, which shows what the
// scope does contain.
function emptyState(c, body) {
  const resources = ["search", "stream"].includes(c.id);
  const next = [
    resources ? el("a", { href: "#/summary", text: "See what this scope contains" }) : null,
    el("a", {
      href: "#", text: "Try another scope",
      onclick: (event) => { event.preventDefault(); document.getElementById("scope").focus(); },
    }),
  ].filter(Boolean);
  return el("div", { class: "empty" },
    el("p", {}, "Nothing matched in ", el("code", { text: state.scope }), "."),
    body.query ? el("p", { class: "muted" }, "Filters sent: ", el("code", { text: body.query })) : null,
    body.suppressed
      ? el("p", { class: "muted", text: `${body.suppressed} low-signal result(s) were hidden; add show_all=true to include them.` })
      : null,
    el("p", {}, next.map((link, i) => [i ? " · " : "", link])));
}

// The response as returned, pretty-printed, collapsed until asked for. A new
// tab is offered for bookmarking or the browser's own viewer, not by default.
function rawBlock(label, text, url) {
  const pre = el("pre", { text });
  return el("details", { class: "raw" },
    el("summary", { text: label }),
    el("div", { class: "raw-actions" },
      copyButton(text, pre),
      el("a", { href: url, target: "_blank", rel: "noopener", text: "Open in new tab" })),
    pre);
}

function table(rows, columns) {
  const first = rows[0] || {};
  const cols = columns || (() => {
    const preferred = PREFERRED_COLUMNS.filter((c) => c in first);
    const chosen = preferred.length ? preferred : Object.keys(first);
    return chosen.slice(0, 6);
  })();
  const shown = rows.slice(0, MAX_ROWS);
  return el("div", {},
    el("div", { class: "table-wrap" },
      el("table", {},
        el("thead", {}, el("tr", {}, cols.map((c) => el("th", { text: c })))),
        el("tbody", {}, shown.map((row) => el("tr", {}, cols.map((c) => cell(c, row[c]))))))),
    rows.length > MAX_ROWS ? el("p", { class: "muted", text: `Showing the first ${MAX_ROWS} of ${rows.length}; Raw JSON has them all.` }) : null);
}

function cell(column, value) {
  if (value === null || value === undefined) return el("td", { class: "muted", text: "—" });
  if ((column === "risk" || column === "highest_risk") && typeof value === "string") {
    return el("td", { class: `risk-${value}`, text: value });
  }
  if (Array.isArray(value)) return el("td", { text: value.map((v) => (typeof v === "object" ? JSON.stringify(v) : v)).join(", ") });
  if (typeof value === "object") return el("td", {}, el("code", { text: JSON.stringify(value) }));
  return el("td", { text: String(value) });
}

// --- Start ---------------------------------------------------------------------------

async function start() {
  document.getElementById("scope-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const value = document.getElementById("scope").value.trim();
    if (value) {
      document.getElementById("scope-status").textContent = "";
      loadCatalogue(value);
    }
  });
  window.addEventListener("hashchange", route);

  try {
    state.session = await getJSON("/docs/_/session");
    renderSession(state.session);
  } catch (error) {
    document.getElementById("session").textContent = `Session unavailable: ${error.message}`;
  }
  const fallback = (state.session && state.session.default_scope) || "projects/YOUR_PROJECT";
  // A remembered scope may no longer be valid; fall back rather than stall.
  if (!(await loadCatalogue(storage("get") || fallback))) await loadCatalogue(fallback);
  if (state.session && !state.session.error) loadProjects();
}

start();
