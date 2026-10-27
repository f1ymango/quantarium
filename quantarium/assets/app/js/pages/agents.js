import { html, useState, useAsync, Loading, ErrorNote } from "../ui.js";
import { get, command } from "../api.js";
import { HelpButton } from "../tutorial/help.js";

// Backticks mark code in the notice; everything else is plain text.
function inline(text) {
  return text.split("`").map((part, index) => (index % 2 ? html`<code>${part}</code>` : part));
}

function Notice({ text }) {
  const blocks = text.split(/\n\s*\n/).map((block) => block.trim()).filter(Boolean);
  return html`<div class="agent-notice">${blocks.map((block) => {
    if (block.startsWith("## ")) return html`<h3>${block.slice(3)}</h3>`;
    if (block.startsWith("# ")) return html`<h2>${block.slice(2)}</h2>`;
    return html`<p>${inline(block)}</p>`;
  })}</div>`;
}

const TIER_TEXT = {
  observe: "Read cells, scripts, scenarios, results, and jobs",
  draft: "Create, edit, and duplicate cell drafts",
  simulate: "Run Static tests, suites, and scenario generation",
  propose: "Ask you to approve consequential changes in the Inbox",
  manage: "Archive, restore, retire cells, and advance Feed experiments without asking",
};

const KIND_LABEL = { external: "External agent", direct: "Model API", bridge: "Agent app" };
const KIND_HINT = {
  external: "For Claude Code, Claude Desktop, the Codex CLI, or any MCP client: they connect with a token through quantarium mcp.",
  direct: "The assistant panel calls a model API: a provider's, a gateway's, or a server on this computer. Any endpoint that speaks OpenAI Chat Completions or the Anthropic Messages API works.",
  bridge: "The assistant panel runs an agent app you installed and signed in to yourself. The app runs the conversation and can use only Quantarium's tools; Quantarium never sees its sign-in.",
};
const PROTOCOL_LABEL = { "openai-chat": "OpenAI-compatible (Chat Completions)", "anthropic-messages": "Anthropic Messages API" };

function hostOf(url) {
  try { return new URL(url).host; } catch { return ""; }
}

// Split a command line into arguments; quotes group words with spaces.
function splitCommand(text) {
  return [...text.matchAll(/"([^"]*)"|(\S+)/g)].map((match) => match[1] ?? match[2]);
}

function PresetCard({ preset, installed }) {
  return html`<div class="agent-preset">
    ${preset.experimental && html`<span class="badge warn" style="margin:0 0 6px">Experimental</span>`}
    <p style="margin:0 0 4px"><strong>Billed to:</strong> ${preset.billing}</p>
    ${preset.conversations && html`<p style="margin:0 0 4px"><strong>Conversations:</strong> ${preset.conversations}</p>`}
    ${preset.terms && html`<p style="margin:0 0 4px"><strong>Terms:</strong> ${preset.terms}</p>`}
    ${preset.note && html`<p class="hint" style="margin:0 0 4px">${preset.note}</p>`}
    ${installed === false && preset.install && html`<p class="notice" style="margin:4px 0">${preset.install}</p>`}
    ${preset.docs && html`<a href=${preset.docs} target="_blank" rel="noopener noreferrer">Documentation</a>`}
  </div>`;
}

function KeyForm({ connection, onDone }) {
  const [key, setKey] = useState("");
  const [error, setError] = useState(null);
  const save = async () => {
    setError(null);
    try { await command("agent-connection-key", { id: connection.id, key }); setKey(""); onDone(); }
    catch (problem) { setError(problem); }
  };
  return html`<div class="row" style="gap:6px">
    <input type="password" value=${key} onInput=${(event) => setKey(event.target.value)} placeholder="Paste the API key"
      autocomplete="off" aria-label=${`API key for ${connection.name}`} />
    <button class="ghost" disabled=${!key.trim()} onClick=${save}>Store in keychain</button>
    <${ErrorNote} error=${error} /></div>`;
}

// Test a model API connection, or list its models and pick one.
function ModelTools({ connection, onChanged }) {
  const [busy, setBusy] = useState(null);
  const [result, setResult] = useState(null);
  const [models, setModels] = useState(null);
  const [error, setError] = useState(null);
  const run = async (what) => {
    setBusy(what); setError(null); setResult(null);
    try {
      if (what === "test") setResult(await command("agent-connection-test", { id: connection.id }));
      else setModels((await command("agent-connection-models", { id: connection.id })).models);
    } catch (problem) { setError(problem); }
    setBusy(null);
  };
  const pick = async (model) => {
    try { await command("agent-connection-model", { id: connection.id, model }); setModels(null); onChanged(); }
    catch (problem) { setError(problem); }
  };
  return html`<div class="agent-model-tools">
    <div class="row" style="gap:4px">
      <button class="ghost" style="padding:0 6px 0 0" disabled=${busy} onClick=${() => run("test")}>${busy === "test" ? "Testing…" : "Test connection"}</button>
      <button class="ghost" style="padding:0" disabled=${busy} onClick=${() => run("models")}>${busy === "models" ? "Listing…" : "Models"}</button></div>
    ${result && html`<p class=${result.ok ? "hint" : "notice"} style="margin:4px 0 0">${result.message} (${result.seconds} s)</p>`}
    ${models && html`<div class="agent-models">${models.length
      ? models.map((model) => html`<button class=${model === connection.model ? "primary" : "ghost"} onClick=${() => pick(model)}>${model}</button>`)
      : html`<span class="muted">The endpoint lists no models; type the name when you create a connection.</span>`}</div>`}
    <${ErrorNote} error=${error} />
  </div>`;
}

// The model's training cutoff, as its provider discloses it; the Lab uses it to judge model memory.
function CutoffField({ connection, onChanged }) {
  const [value, setValue] = useState(connection.training_cutoff || "");
  const [error, setError] = useState(null);
  const save = async (next) => {
    setError(null);
    try { await command("agent-connection-cutoff", { id: connection.id, training_cutoff: next || null }); onChanged(); }
    catch (problem) { setError(problem); }
  };
  return html`<div class="row" style="gap:4px;margin-top:4px">
    <label class="faint" style="font-size:12px">Training cutoff
      <input type="date" value=${value} onInput=${(event) => setValue(event.target.value)} style="margin-left:4px" /></label>
    <button class="ghost" style="padding:0 4px" disabled=${value === (connection.training_cutoff || "")} onClick=${() => save(value)}>Save</button>
    <${ErrorNote} error=${error} /></div>`;
}

function DailyLimitField({ connection, onChanged }) {
  const [value, setValue] = useState(connection.daily_cost_limit_usd ?? "");
  const [error, setError] = useState(null);
  const save = async () => {
    setError(null);
    try { await command("agent-connection-daily-limit", { id: connection.id, usd: value === "" ? null : Number(value) }); onChanged(); }
    catch (problem) { setError(problem); }
  };
  return html`<div class="row" style="gap:4px;margin-top:4px">
    <label class="faint" style="font-size:12px">Daily cost limit, USD
      <input type="number" min="0.01" step="any" value=${value} placeholder="none" onInput=${(event) => setValue(event.target.value)} style="margin-left:4px;width:90px" /></label>
    <button class="ghost" style="padding:0 4px" disabled=${String(value) === String(connection.daily_cost_limit_usd ?? "")} onClick=${save}>Save</button>
    <${ErrorNote} error=${error} /></div>`;
}

function ConnectionKind({ item, presets, onChanged, onReplaceKey }) {
  if (item.kind === "direct") {
    const preset = presets.api.find((entry) => entry.id === item.preset);
    return html`${preset && preset.id !== "custom" ? preset.name : PROTOCOL_LABEL[item.protocol]}
      <br /><span class="mono faint">${item.model}</span>
      <br /><span class="faint">at ${hostOf(item.endpoint)}</span>
      ${item.key_env ? html`<br /><span class="faint">key from ${item.key_env}</span>` : ""}
      ${item.key_store === "keyring" ? html`<br /><button class="ghost" style="padding:0" onClick=${onReplaceKey}>Replace key</button>` : ""}
      <${ModelTools} connection=${item} onChanged=${onChanged} />
      <${CutoffField} key=${`${item.id}:${item.model}`} connection=${item} onChanged=${onChanged} />
      <${DailyLimitField} key=${`${item.id}:limit`} connection=${item} onChanged=${onChanged} />`;
  }
  if (item.kind === "bridge") {
    const preset = presets.runtimes.find((entry) => entry.id === item.runtime);
    return html`${preset ? preset.name : item.runtime}
      ${preset?.experimental && html` <span class="badge warn">experimental</span>`}
      ${item.command && html`<br /><span class="mono faint">${item.command.join(" ")}</span>`}
      ${item.acknowledged_utc && html`<br /><span class="faint">acknowledged ${new Date(item.acknowledged_utc).toLocaleDateString()}</span>`}`;
  }
  return KIND_LABEL.external;
}

function Connections({ data, onChanged }) {
  const capabilities = data.assistant || {};
  const presets = data.presets || { api: [], runtimes: [] };
  const [kind, setKind] = useState("external");
  const [apiId, setApiId] = useState(presets.api[0]?.id || "custom");
  const [protocol, setProtocol] = useState("openai-chat");
  const [endpoint, setEndpoint] = useState(presets.api[0]?.endpoint || "");
  const [model, setModel] = useState(presets.api[0]?.models[0] || "");
  const [keyWhere, setKeyWhere] = useState("env");
  const [keyEnv, setKeyEnv] = useState(presets.api[0]?.key_env || "");
  const [runtimeId, setRuntimeId] = useState(presets.runtimes[0]?.id || "codex");
  const [commandText, setCommandText] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [stored, setStored] = useState(null);
  const [name, setName] = useState("");
  const [tiers, setTiers] = useState(data.default_tiers);
  const [editUser, setEditUser] = useState(false);
  const [created, setCreated] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const active = data.connections.filter((item) => !item.revoked_utc);
  const api = presets.api.find((item) => item.id === apiId) || presets.api[0];
  const runtime = presets.runtimes.find((item) => item.id === runtimeId) || presets.runtimes[0];
  const effectiveProtocol = api?.protocol || protocol;
  const sdkMissing = effectiveProtocol === "anthropic-messages" ? !capabilities.anthropic_sdk : !capabilities.openai_sdk;
  // A key goes to a host the preset doesn't name only after the user has seen it.
  const keyHost = hostOf(endpoint);
  const unfamiliarHost = keyWhere !== "none" && keyHost && keyHost !== hostOf(api?.endpoint || "");
  const toggle = (tier) => setTiers(tiers.includes(tier) ? tiers.filter((item) => item !== tier) : [...tiers, tier]);
  const choosePreset = (id) => {
    const next = presets.api.find((item) => item.id === id);
    setApiId(id); setEndpoint(next?.endpoint || ""); setModel(next?.models[0] || "");
    setKeyEnv(next?.key_env || ""); setKeyWhere(next?.key === "none" ? "none" : "env");
  };
  const chooseRuntime = (id) => { setRuntimeId(id); setAcknowledged(false); setCommandText(""); };
  const ready = name.trim() && (kind !== "direct" || (model.trim() && endpoint.trim() && (keyWhere !== "env" || keyEnv)))
    && (kind !== "bridge" || ((!runtime?.acknowledgement || acknowledged) && (runtime?.command || splitCommand(commandText).length)));
  const create = async () => {
    setBusy(true); setError(null);
    try {
      const keySetting = keyWhere === "env" ? { key_env: keyEnv } : { key_store: keyWhere };
      const settings = kind === "direct"
        ? { preset: api.id, ...(api.protocol ? {} : { protocol }), endpoint: endpoint.trim(), model: model.trim(), ...keySetting }
        : kind === "bridge" ? { runtime: runtime.id,
            ...(runtime.command ? {} : { command: splitCommand(commandText) }),
            ...(runtime.acknowledgement ? { acknowledgement: runtime.acknowledgement } : {}) }
        : undefined;
      const result = await command("agent-connection-create", { name, tiers, edit_user_scripts: editUser, kind, provider: settings });
      if (result.token) setCreated(result);
      if (kind === "direct" && keyWhere === "keyring") setStored(result.connection);
      setName(""); setEditUser(false); setAcknowledged(false); onChanged();
    }
    catch (problem) { setError(problem); }
    setBusy(false);
  };
  const revoke = async (item) => {
    if (!confirm(`Revoke ${item.name}? It stops working immediately${item.key_store === "keyring" ? ", and its stored key is removed" : ""}.`)) return;
    try { await command("agent-connection-revoke", { id: item.id }); onChanged(); } catch (problem) { setError(problem); }
  };
  return html`<div class="panel">
    <h2 style="margin:0 0 4px">Connections</h2>
    <p class="muted" style="margin:0 0 8px">Each connection has its own token and its own permissions. Quantarium checks them on every request, whatever the model asks for.</p>
    <p class="hint" style="margin:0 0 12px">These limits cover what an agent does through Quantarium. If an agent also has its own terminal or file access on this computer, such as Claude Code with its shell tools, Quantarium can't limit that; keep that tool's permission prompts on.</p>
    <${ErrorNote} error=${error} />
    ${created && html`<div class="agent-token">
      <strong>Token for ${created.connection.name}</strong>
      <p class="hint" style="margin:4px 0 8px">Copy it now. Quantarium keeps only a fingerprint of it, so it can't be shown again.</p>
      <div class="row"><code class="mono">${created.token}</code>
        <button class="ghost" onClick=${() => navigator.clipboard?.writeText(created.token)}>Copy</button>
        <button class="ghost" onClick=${() => setCreated(null)}>Done</button></div>
    </div>`}
    ${stored && html`<div class="agent-token"><strong>Store the API key for ${stored.name}</strong>
      <p class="hint" style="margin:4px 0 8px">It goes to your system keychain. Quantarium never saves it in a file or shows it again.</p>
      <${KeyForm} connection=${stored} onDone=${() => setStored(null)} /></div>`}
    ${active.length ? html`<table class="data"><thead><tr><th>Name</th><th>Kind</th><th>Connects to</th><th>Permissions</th><th>Budget</th><th></th></tr></thead>
      <tbody>${active.map((item) => html`<tr><td>${item.name}<br /><span class="faint">${new Date(item.created_utc).toLocaleDateString()}</span></td>
        <td>${KIND_LABEL[item.kind || "external"]}</td>
        <td><${ConnectionKind} item=${item} presets=${presets} onChanged=${onChanged} onReplaceKey=${() => setStored(item)} /></td>
        <td>${item.tiers.map((tier) => html`<span class="badge neutral" style="margin:0 4px 4px 0">${tier}</span>`)}
          ${item.edit_user_scripts && html`<span class="badge warn" style="margin:0 4px 4px 0">edits your scripts</span>`}</td>
        <td class="muted">${((budget) => `${budget.jobs_per_hour} jobs/hour, ${budget.cells_per_day} cells/day`)({ ...data.default_budgets, ...(item.budgets || {}) })}</td>
        <td class="num"><button class="ghost" onClick=${() => revoke(item)}>Revoke</button></td></tr>`)}</tbody></table>`
      : html`<p class="muted">No connections yet.</p>`}
    <div class="agent-new">
      <h3 style="margin:16px 0 8px">New connection</h3>
      <div class="row" style="gap:6px;margin:0 0 10px">
        ${["external", "direct", "bridge"].map((value) => html`<button class=${kind === value ? "primary" : "ghost"} onClick=${() => setKind(value)}>${KIND_LABEL[value]}</button>`)}</div>
      <p class="hint" style="margin:0 0 10px">${KIND_HINT[kind]}</p>
      ${kind === "direct" && api && html`<div class="agent-provider">
        <label class="agent-confirm" style="margin:0"><span>Provider</span>
          <select value=${apiId} onChange=${(event) => choosePreset(event.target.value)}>
            ${presets.api.map((item) => html`<option value=${item.id}>${item.name}</option>`)}</select></label>
        <${PresetCard} preset=${api} />
        ${!api.protocol && html`<label class="agent-confirm" style="margin:0"><span>Protocol</span>
          <select value=${protocol} onChange=${(event) => setProtocol(event.target.value)}>
            ${Object.entries(PROTOCOL_LABEL).map(([value, text]) => html`<option value=${value}>${text}</option>`)}</select></label>`}
        ${sdkMissing && html`<p class="notice">The SDK for this protocol isn't installed. Run <span class="mono">py -m pip install "quantarium[assistant]"</span>, then restart Quantarium.</p>`}
        <label class="agent-confirm" style="margin:0"><span>Endpoint</span>
          <input value=${endpoint} onInput=${(event) => setEndpoint(event.target.value)} class="mono" maxlength="300"
            placeholder="https://api.example.com/v1" /></label>
        ${unfamiliarHost && html`<p class="notice" style="margin:0">Your key will be sent to <strong>${keyHost}</strong>. Make sure you trust it.</p>`}
        <label class="agent-confirm" style="margin:0"><span>Model</span>
          <input value=${model} onInput=${(event) => setModel(event.target.value)} list="agent-model-list" maxlength="200"
            placeholder="The model name; after creating, Models lists what the endpoint offers" />
          <datalist id="agent-model-list">${api.models.map((item) => html`<option value=${item} />`)}</datalist></label>
        <label class="agent-confirm" style="margin:0"><span>API key</span>
          <select value=${keyWhere} onChange=${(event) => setKeyWhere(event.target.value)}>
            <option value="env">Read it from an environment variable</option>
            <option value="keyring" disabled=${!capabilities.keyring}>Store it in the system keychain${capabilities.keyring ? "" : " (needs quantarium[assistant])"}</option>
            ${api.key !== "required" && html`<option value="none">No key (a server on this computer)</option>`}</select></label>
        ${keyWhere === "env" && html`<label class="agent-confirm" style="margin:0"><span>Environment variable</span>
          <input value=${keyEnv} onInput=${(event) => setKeyEnv(event.target.value.toUpperCase())} class="mono" maxlength="64" placeholder="EXAMPLE_API_KEY" /></label>
          <p class="hint" style="margin:0">Set it where you start <span class="mono">quantarium serve</span>. Never paste a key into a chat or a file.</p>`}
      </div>`}
      ${kind === "bridge" && runtime && html`<div class="agent-provider">
        <label class="agent-confirm" style="margin:0"><span>Agent app</span>
          <select value=${runtimeId} onChange=${(event) => chooseRuntime(event.target.value)}>
            ${presets.runtimes.map((item) => html`<option value=${item.id}>${item.name}${item.experimental ? " (experimental)" : ""}</option>`)}</select></label>
        <${PresetCard} preset=${runtime} installed=${runtime.command ? capabilities.apps?.[runtime.id] : undefined} />
        ${!runtime.command && html`<label class="agent-confirm" style="margin:0"><span>Command</span>
          <input value=${commandText} onInput=${(event) => setCommandText(event.target.value)} class="mono" maxlength="1000"
            placeholder="opencode acp" /></label>
          <p class="hint" style="margin:0">Quantarium runs this command for each turn, with no shell, in an empty folder. If the app reports using a tool that isn't Quantarium's, Quantarium stops the turn.</p>`}
        ${runtime.acknowledgement && html`<label class="agent-tier agent-acknowledge"><input type="checkbox" checked=${acknowledged} onChange=${() => setAcknowledged(!acknowledged)} />
          <span>${runtime.acknowledgement}</span></label>`}
      </div>`}
      <label class="agent-confirm" style="margin:0 0 10px"><span>Name</span>
        <input value=${name} onInput=${(event) => setName(event.target.value)} placeholder=${kind === "external" ? "Claude Code" : kind === "direct" ? `${api?.name || "Model"} in Quantarium` : runtime?.name || "Agent app"} maxlength="60" /></label>
      ${data.tiers.map((tier) => html`<label class="agent-tier"><input type="checkbox" checked=${tiers.includes(tier)} onChange=${() => toggle(tier)} />
        <span><strong>${tier}</strong> <span class="muted">${TIER_TEXT[tier] || ""}</span></span></label>`)}
      <label class="agent-tier"><input type="checkbox" checked=${editUser} onChange=${() => setEditUser(!editUser)} />
        <span><strong>edit your scripts</strong> <span class="muted">Without this, the agent writes scripts only in strategies/agent/. Either way, scripts an agent writes run only in the sandbox until you review them.</span></span></label>
      <div class="row" style="justify-content:flex-end"><button class="primary" disabled=${busy || !ready} onClick=${create}>Create connection</button></div>
    </div>
  </div>`;
}

const OUTCOME = { ok: "up", refused: "down", error: "warn" };

function AgentScripts() {
  const listing = useAsync(() => get("agents/scripts"), []);
  const [open, setOpen] = useState(null);
  const [source, setSource] = useState(null);
  const [error, setError] = useState(null);
  const pending = (listing.data || []).filter((item) => item.state !== "reviewed" && item.current);
  const show = async (item) => {
    setError(null); setOpen(item.sha256); setSource(null);
    try {
      const script = await get(`scripts/${item.key}`);
      setSource(script.sha256 === item.sha256 ? script.source : null);
    } catch (problem) { setError(problem); }
  };
  const review = async (item) => {
    try { await command("script-review", { sha256: item.sha256 }); setOpen(null); listing.reload(); }
    catch (problem) { setError(problem); }
  };
  return html`<div class="panel">
    <h2 style="margin:0 0 4px">Scripts written by agents</h2>
    <p class="muted" style="margin:0 0 12px">Until you review it, a script an agent wrote runs only in the sandbox during Static tests, cut off from your files, the network, and your keys. Feed needs your review. Editing it yourself also makes it your own code.</p>
    <${ErrorNote} error=${error} />
    ${pending.length ? html`<table class="data"><thead><tr><th>Script</th><th>Written by</th><th>When</th><th></th></tr></thead>
      <tbody>${pending.map((item) => html`<tr><td class="mono">${item.key}</td><td>${item.connection_name}</td>
        <td>${new Date(item.written_utc).toLocaleString()}</td>
        <td class="num"><button class="ghost" onClick=${() => show(item)}>Read</button></td></tr>
        ${open === item.sha256 && html`<tr><td colspan="4">
          ${source === null ? html`<p class="muted">Loading…</p>` : html`<pre class="agent-source">${source}</pre>
            <div class="row" style="justify-content:flex-end;gap:8px">
              <button class="ghost" onClick=${() => setOpen(null)}>Close</button>
              <button class="primary" onClick=${() => review(item)}>I've read it; allow it to run</button></div>`}
        </td></tr>`}`)}</tbody></table>`
      : html`<p class="muted">Nothing waiting for review.</p>`}
  </div>`;
}

function AgentLog() {
  const log = useAsync(() => get("agents/log", { limit: 50 }), []);
  return html`<div class="panel flush">
    <div class="row between" style="padding:16px 18px 0"><h2 style="margin:0">Agent activity</h2>
      <button class="ghost" onClick=${log.reload}>Refresh</button></div>
    <p class="hint" style="padding:0 18px">Every agent request, including refused ones, newest first. Secrets are removed before saving.</p>
    ${log.error ? html`<div style="padding:0 18px 16px"><${ErrorNote} error=${log.error} /></div>`
      : log.data?.length ? html`<div class="table-wrap"><table class="data"><thead><tr><th>Time</th><th>Connection</th><th>Request</th><th>Outcome</th></tr></thead>
        <tbody>${log.data.map((entry) => html`<tr><td>${new Date(entry.time_utc).toLocaleString()}</td>
          <td>${entry.connection_name || html`<span class="faint">unknown token</span>`}</td>
          <td class="mono">${entry.kind} ${entry.name}</td>
          <td><span class=${`badge ${OUTCOME[entry.outcome] || "neutral"}`}>${entry.outcome}</span>
            ${entry.message && html` <span class="muted">${entry.message}</span>`}</td></tr>`)}</tbody></table></div>`
      : html`<p class="muted" style="padding:0 18px 16px">No agent requests yet.</p>`}
  </div>`;
}

export function AgentsPage() {
  const status = useAsync(() => get("agents"), []);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  // Keep the page while reloading, so a just-created token stays on screen.
  if (status.loading && !status.data) return html`<div class="content"><${Loading} /></div>`;
  if (status.error) return html`<div class="content"><${ErrorNote} error=${status.error} /></div>`;
  const data = status.data;
  const matches = typed.trim().replace(/\s+/g, " ").toLowerCase() === data.confirmation.toLowerCase();
  const run = async (name, params) => {
    setBusy(true); setError(null);
    try { await command(name, params); setTyped(""); status.reload(); } catch (problem) { setError(problem); }
    setBusy(false);
  };

  return html`<div class="content agents-page">
    <div><div class="row"><h1>Agents</h1><${HelpButton} topic="agents" /></div>
      <p class="muted" style="margin:4px 0 0">Let an AI model help you build, test, and manage cells in this workspace.</p></div>
    <${ErrorNote} error=${error} />
    ${data.enabled ? html`<div class="panel">
        <div class="row between"><div>${data.paused ? html`<span class="badge down">Paused</span>` : html`<span class="badge up">On</span>`}
          <h2 style="margin:8px 0 4px">${data.paused ? "All agent connections are paused" : "Agent features are on"}</h2>
          <p class="muted" style="margin:0">You accepted notice version ${data.accepted_version} on ${new Date(data.accepted_utc).toLocaleString()}.</p></div>
          <div class="row">
            <button class=${data.paused ? "primary" : "ghost"} disabled=${busy}
              onClick=${() => run(data.paused ? "agents-resume" : "agents-pause", {})}>${data.paused ? "Resume all" : "Pause all"}</button>
            <button class="ghost" disabled=${busy} onClick=${() => run("agents-disable", {})}>Turn off agent features</button></div></div>
        <p class="hint">External agents connect with a token through <span class="mono">quantarium mcp</span>. The assistant panel uses a model API or an agent app connection.</p>
      </div>
      <${Connections} data=${data} onChanged=${status.reload} />
      <${AgentScripts} />
      <${AgentLog} />
      <details class="panel"><summary>Read the notice again</summary><${Notice} text=${data.notice} /></details>`
    : html`<div class="panel">
        <span class="badge neutral">Off</span>
        <h2 style="margin:8px 0 4px">Agent features are off</h2>
        <p class="muted" style="margin:0">${data.needs_new_acceptance
          ? "The notice has changed since you last accepted it, so agent features were turned off. Read the new notice to turn them back on."
          : "They stay off until you read the notice below and accept it. Nothing in this workspace is sent anywhere while they are off."}</p>
      </div>
      <div class="panel">
        <${Notice} text=${data.notice} />
        <label class="agent-confirm">
          <span>To turn on agent features, type <strong>${data.confirmation}</strong></span>
          <input value=${typed} onInput=${(event) => setTyped(event.target.value)} autocomplete="off" spellcheck="false"
            aria-label="Confirmation" />
        </label>
        <div class="row" style="justify-content:flex-end">
          <button class="primary" disabled=${busy || !matches}
            onClick=${() => run("agents-enable", { confirmation: typed, notice_version: data.notice_version })}>Turn on agent features</button>
        </div>
      </div>`}
  </div>`;
}
