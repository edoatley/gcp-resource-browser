// The gcpe capability browser. Renders /docs/_/catalogue; "Call API" fetches the
// example's own /v1 URL, exactly as any API client would. Every server value
// reaches the page through textContent, never parsed as markup.
"use strict";

const state = { session: null, catalogue: null, scope: null, forms: {}, tab: 0 };
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
    builder(c),
  ];
}

// --- The builder -------------------------------------------------------------------
//
// One form per capability, generated from its parameters. Examples are presets
// that fill it. Every keystroke asks the server to render the three forms
// (/docs/_/render): the page never assembles a command itself, so the forms
// here are the same ones the tests pin against --show-gcloud.

const RENDER_DELAY_MS = 250;
const FORM_TABS = ["CLI", "API", "gcloud"];

// The scope param is the header's when the route requires it. When it is
// optional (roles), it is a switch: "also include the scope above".
function scopeIsBound(p) { return p.api === "scope" && p.required; }
function scopeIsSwitch(p) { return p.api === "scope" && !p.required; }

function valuesFromArgs(c, args) {
  const values = {};
  for (const p of c.params) {
    if (scopeIsBound(p)) continue;
    if (scopeIsSwitch(p)) values.scope = "scope" in args;
    else if (p.api in args) values[p.api] = args[p.api];
  }
  return values;
}

function builder(c) {
  const saved = state.forms[c.id];
  const first = c.examples.find((ex) => !ex.unavailable) || c.examples[0];
  const form = saved || { values: valuesFromArgs(c, first.args), preset: first.id };
  state.forms[c.id] = form;

  const fields = el("div", { class: "fields" });
  const tabs = el("div", { class: "tabs", role: "tablist" });
  const body = el("div", { class: "form" });
  const errors = el("div", { class: "errors", "aria-live": "polite" });
  const result = el("div", { class: "result", "aria-live": "polite" });
  const call = el("button", { type: "button", class: "primary", text: "Call API", disabled: true });
  let rendered = null;
  let timer = null;

  const showTab = () => {
    // Without a CLI form, open on the API rather than on a "no CLI" note.
    const index = !c.cli_command && state.tab === 0 ? 1 : state.tab;
    tabs.querySelectorAll("button").forEach((b, i) => b.setAttribute("aria-selected", String(i === index)));
    if (!rendered) return put(body, el("p", { text: "Rendering…" }));
    const key = ["cli", "url", "gcloud"][index];
    const text = key === "url" ? window.location.origin + rendered.url : rendered[key];
    put(body, text
      ? [el("pre", {}, el("code", { text })), copyButton(text)]
      : el("p", { text: rendered.notes[key] || "Not available." }));
  };
  FORM_TABS.forEach((label, i) => tabs.append(el("button", {
    type: "button", role: "tab", text: label,
    onclick: () => { state.tab = i; showTab(); },
  })));

  const render = async () => {
    const query = new URLSearchParams({ capability: c.id });
    for (const p of c.params) {
      const value = scopeIsBound(p) ? state.scope : form.values[p.api];
      if (scopeIsSwitch(p)) { if (value) query.append("scope", state.scope); continue; }
      if (Array.isArray(value)) value.forEach((v) => query.append(p.api, v));
      else if (value === true) query.append(p.api, "true");
      else if (value !== undefined && value !== false && value !== "") query.append(p.api, value);
    }
    try {
      rendered = await getJSON(`/docs/_/render?${query}`);
    } catch (error) {
      rendered = { cli: null, url: "", gcloud: null, notes: {}, errors: [error.message] };
    }
    put(errors, rendered.errors.length
      ? el("ul", { class: "error" }, rendered.errors.map((e) => el("li", { text: e })))
      : null);
    call.disabled = rendered.errors.length > 0;
    call.title = rendered.errors.length ? "Fix the errors above first" : `GET ${rendered.url}`;
    showTab();
  };
  const changed = () => {
    form.preset = null;
    markPreset();
    clearTimeout(timer);
    timer = setTimeout(render, RENDER_DELAY_MS);
  };

  const presets = el("div", { class: "presets" },
    el("span", { class: "muted", text: "Start from an example:" }),
    c.examples.map((ex) => el("button", {
      type: "button", class: "preset", "data-id": ex.id, text: ex.title,
      disabled: Boolean(ex.unavailable), title: ex.unavailable || "",
      onclick: () => {
        form.values = valuesFromArgs(c, ex.args);
        form.preset = ex.id;
        put(fields, c.params.map((p) => field(c, p, form, changed)));
        put(result);
        markPreset();
        render();
      },
    })));
  const markPreset = () => presets.querySelectorAll("button").forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset.id === form.preset)));

  call.addEventListener("click", () => {
    if (rendered) callApi(c, { url: rendered.url, streams: c.route.endsWith("/stream") }, result, call);
  });

  put(fields, c.params.map((p) => field(c, p, form, changed)));
  markPreset();
  render();

  return el("section", { class: "builder", id: "builder" },
    el("h2", { text: "Build a call" }),
    presets,
    c.params.length ? fields : el("p", { class: "muted", text: "This capability takes no parameters." }),
    // Docked to the bottom of the screen while the form is in view, so an edit
    // anywhere in a long form shows its effect without scrolling.
    el("div", { class: "dock" },
      el("h3", { text: "The same call, three ways" }),
      tabs, body, errors,
      el("div", { class: "actions" }, call,
        el("span", { class: "muted", text: "Always runs the API form, whichever tab is showing." }))),
    result);
}

function field(c, p, form, changed) {
  const id = `f-${c.id}-${p.api}`;
  const label = el("label", { for: id },
    el("code", { text: p.api }),
    p.required ? el("span", { class: "badge", text: "required" }) : null,
    p.repeatable ? el("span", { class: "badge", text: "repeatable" }) : null);
  const flag = p.cli ? el("span", { class: "flag" }, "CLI ", el("code", { text: p.cli })) : el("span", { class: "flag", text: "API only" });

  if (scopeIsBound(p)) {
    return el("div", { class: "field" }, label,
      el("div", {}, el("code", { text: state.scope }), el("span", { class: "muted", text: " · from the Scope box above" }), flag,
        el("small", { text: p.help })));
  }

  let input;
  if (scopeIsSwitch(p) || p.kind === "bool") {
    input = el("input", { id, type: "checkbox", checked: Boolean(form.values[p.api]) });
    input.addEventListener("change", () => { form.values[p.api] = input.checked; changed(); });
    const text = scopeIsSwitch(p) ? ` Use ${state.scope}` : " Yes";
    input = el("label", { class: "check" }, input, text);
  } else if (p.kind === "list") {
    input = listInput(id, p, form, changed);
  } else {
    input = el("input", {
      id, type: p.kind === "str" ? "text" : "number", spellcheck: "false",
      min: p.kind === "int" ? "1" : p.kind === "float" ? "0" : null,
      list: p.suggestions.length ? `${id}-options` : null,
      value: form.values[p.api] ?? "",
    });
    input.addEventListener("input", () => { form.values[p.api] = input.value; changed(); });
    if (p.suggestions.length) input = [input, options(`${id}-options`, p.suggestions)];
  }
  return el("div", { class: "field" }, label, el("div", {}, input, flag, el("small", { text: p.help })));
}

// One input per value, with an empty one always waiting at the end. Each can
// offer suggestions, which a single comma-separated box could not.
function listInput(id, p, form, changed) {
  const box = el("div", { class: "list" });
  const listId = p.suggestions.length ? `${id}-options` : null;
  const sync = () => {
    form.values[p.api] = [...box.querySelectorAll("input")].map((i) => i.value.trim()).filter(Boolean);
    const inputs = [...box.querySelectorAll("input")];
    if (inputs[inputs.length - 1].value) box.append(entry(""));
    changed();
  };
  const entry = (value) => {
    const input = el("input", { type: "text", spellcheck: "false", list: listId, value, placeholder: "add a value" });
    input.addEventListener("input", sync);
    return input;
  };
  const values = form.values[p.api] || [];
  values.forEach((v, i) => box.append(i === 0 ? Object.assign(entry(v), { id }) : entry(v)));
  box.append(values.length ? entry("") : Object.assign(entry(""), { id }));
  return [box, listId ? options(listId, p.suggestions) : null];
}

function options(id, values) {
  return el("datalist", { id }, values.map((v) => el("option", { value: v })));
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
    el("a", {
      href: "#builder", text: "Edit the filters above",
      onclick: (event) => {
        event.preventDefault();
        const first = document.querySelector(".builder .fields input");
        if (first) first.focus();
      },
    }),
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
