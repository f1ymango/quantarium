import { html, useState, useEffect, useMemo, useAsync, Change, Metric, ModeBadge, EvidenceBadge, Loading, ErrorNote, fmt, Icon, Dialog, navigate } from "../ui.js";
import { get, command, runJob } from "../api.js";
import { tutorialActive, tutorialNames, TUTORIAL_FEED_THROUGH } from "../tutorial/state.js";
import { ChartView, TileView, AggregateView, SleevePanel } from "./cell-views.js";
import { HelpButton } from "../tutorial/help.js";

const TABS = [["overview", "Overview"], ["configure", "Configure"], ["runs", "Runs"], ["timeline", "Timeline"]];

function resultLabel(result, index) {
  const d = result?.deployment || {};
  const kind = { static: "Replay", suite: "Scenarios", feed: "Feed" }[d.kind] || d.kind;
  return `#${index + 1} · ${kind} · ${d.label || d.id} · v${d.version} · ${fmt.date(d.created_utc)}`;
}

export function CellPage({ id, query }) {
  const [tab, setTab] = useState(query.tab || "overview");
  const [pick, setPick] = useState(null);
  const [deploying, setDeploying] = useState(false);
  const cell = useAsync(() => get(`cells/${id}`), [id]);
  const scripts = useAsync(() => get("scripts"), []);
  const workspace = useAsync(() => get("workspace"), []);
  const inboxes = useAsync(() => get("decision-inboxes"), []);
  const deciders = useAsync(() => get("deciders"), []);
  useEffect(() => setTab(query.tab || (cell.data?.card.status === "draft" ? "configure" : "overview")), [query.tab, cell.data?.id]);

  if (cell.loading && !cell.data) return html`<div class="content"><${Loading} /></div>`;
  if (cell.error) return html`<div class="content"><${ErrorNote} error=${cell.error} /><a href="#/cells">Back to your cells</a></div>`;
  const record = cell.data;
  const results = record.results.map((result, index) => ({ ...result, index })).filter(Boolean);
  const chosen = pick === null ? results[results.length - 1] : results.find((item) => item.index === pick);

  const rename = async (name) => {
    if (!name.trim() || name === record.name) return;
    try { await command("cell-save", { id, name }); cell.reload(); } catch (problem) { alert(problem.message); cell.reload(); }
  };
  const showTab = (key) => { setTab(key); history.replaceState(null, "", `#/cells/${id}?tab=${key}`); };

  return html`<div class="content">
    <a href="#/home" class="row faint" style="gap:4px"><${Icon} name="back" size=${16} /> Home</a>
    <div class="cell-head">
      <input data-tour="cell-name" class="name-input" aria-label="Cell name" value=${record.name}
        onBlur=${(event) => rename(event.target.value)} onKeyDown=${(event) => event.key === "Enter" && event.target.blur()} />
      <${HelpButton} topic=${record.spec.mode === "complex" ? "complex" : "cell-mode"} label="Cell" /><${ModeBadge} mode=${record.spec.mode} /><span class="badge neutral">v${record.version}</span>
      <span class="spacer" style="flex:1"></span>
      <button onClick=${async () => { const [copy] = await command("cell-duplicate", { ids: [id] }); navigate(`cells/${copy.id}?tab=configure`); }}>Duplicate</button>
      <button data-tour="cell-run" class="primary" onClick=${() => setDeploying(true)} disabled=${record.card.problems.length > 0}
        title=${record.card.problems.join("; ") || "Run this cell"}><${Icon} name="play" size=${14} /> Run</button>
    </div>
    ${record.card.problems.length > 0 && html`<div class="notice info">To run this cell: ${record.card.problems.join(" · ")}.</div>`}
    <div class="tabs" role="tablist">${[...TABS, ...(record.spec.ask ? [["answers", "Answers"]] : [])].map(([key, label]) => html`<button role="tab" aria-selected=${tab === key}
      data-tour=${`tab-${key}`} class=${`tab ${tab === key ? "active" : ""}`} onClick=${() => showTab(key)}>${label}</button>`)}</div>
    ${tab === "overview" && html`<${Overview} record=${record} results=${results} chosen=${chosen} onPick=${setPick}
      onRun=${() => setDeploying(true)} onConfigure=${() => showTab("configure")} />`}
    ${tab === "configure" && html`<${Configure} record=${record} scripts=${scripts.data || []}
      models=${workspace.data?.models || []} decisionFiles=${workspace.data?.decisions || []} inboxes=${inboxes.data || []} deciders=${deciders.data || []}
      onSourcesChanged=${() => { inboxes.reload(); deciders.reload(); }} onSaved=${cell.reload} />`}
    ${tab === "runs" && html`<${Runs} results=${results} onOpen=${(index) => { setPick(index); showTab("overview"); }} />`}
    ${tab === "timeline" && html`<${Timeline} results=${results} chosen=${chosen} onPick=${setPick} />`}
    ${tab === "answers" && record.spec.ask && html`<${Answers} record=${record} />`}
    ${deploying && html`<${DeployDialog} record=${record} onClose=${() => setDeploying(false)}
      onDone=${() => { setDeploying(false); setPick(null); cell.reload(); showTab("overview"); }} />`}
  </div>`;
}

function ResultPicker({ results, chosen, onPick }) {
  if (results.length < 2) return null;
  return html`<select aria-label="Result to show" value=${chosen?.index} onChange=${(event) => onPick(Number(event.target.value))}>
    ${results.map((result) => html`<option value=${result.index}>${resultLabel(result, result.index)}</option>`)}</select>`;
}

// Where a result's holdings live: Static and Feed point at their run; a suite
// needs one of its scenario runs.
function useRunTarget(chosen) {
  const [scenario, setScenario] = useState(null);
  const suite = useAsync(() => (chosen?.deployment.kind === "suite"
    ? get("suite-report", { config: chosen.deployment.config }) : Promise.resolve(null)), [chosen?.deployment.config, chosen?.index]);
  if (!chosen || chosen.error) return { target: null };
  const d = chosen.deployment;
  if (d.kind !== "suite") return { target: { db: d.db, kind: d.kind, id: d.id, cell: chosen.cell_name } };
  const rows = (suite.data?.matrix || []).filter((row) => row.cell === chosen.cell_name);
  const row = rows.find((item) => item.scenario === scenario) || rows[0];
  return { rows, row, setScenario, loading: suite.loading,
    target: row ? { db: d.db, kind: "static", id: row.run_id, cell: chosen.cell_name } : null };
}

function Overview({ record, results, chosen, onPick, onRun, onConfigure }) {
  const run = useRunTarget(chosen);
  const key = run.target ? JSON.stringify(run.target) : "";
  const holdings = useAsync(() => (run.target ? get("holdings", run.target) : Promise.resolve(null)), [key]);
  if (!results.length) {
    return html`<div class="panel empty"><h2 style="margin:0">No results yet</h2>
      <p class="muted" style="margin:0">${record.card.problems.length ? "Finish configuring this cell, then run it." : "Run this cell on data, scenarios, or a forward test."}</p>
      ${record.card.problems.length ? html`<button onClick=${onConfigure}>Configure</button>` : html`<button class="primary" onClick=${onRun}>Run</button>`}</div>`;
  }
  const source = run.row || chosen;
  const view = holdings.data?.view;
  const views = { chart: ChartView, tiles: TileView, aggregate: AggregateView };
  const View = views[view];
  return html`<div class="stack">
    <div class="row between">
      <div class="row"><${EvidenceBadge} evidence=${chosen.evidence} />
        ${chosen.deployment.kind === "feed" && chosen.forward_sessions !== undefined && html`<span class="faint">${chosen.forward_sessions} forward sessions</span>`}
        ${run.rows?.length > 1 && html`<select aria-label="Scenario" value=${run.row?.scenario} onChange=${(event) => run.setScenario(event.target.value)}>
          ${run.rows.map((row) => html`<option value=${row.scenario}>${row.scenario} (${row.kind})</option>`)}</select>`}</div>
      <${ResultPicker} results=${results} chosen=${chosen} onPick=${onPick} />
    </div>
    ${chosen.error ? html`<${ErrorNote} error=${chosen.error} />` : html`<div data-tour="cell-metrics"><div class="row" style="margin-bottom:6px"><span class="faint">Performance</span></div><div class="summary-strip">
      <${Metric} label="Return" big=${true}><${Change} value=${source.total_return_pct} /><//>
      <${Metric} label="Max drawdown">${fmt.num(source.max_drawdown_pct, 1)}%<//>
      <${Metric} label="Volatility">${fmt.num(source.annualized_volatility_pct, 1)}%<//>
      <${Metric} label="Sharpe">${fmt.num(source.sharpe_ratio_rf0, 2)}<//>
      ${source.turnover_ratio !== undefined && html`<${Metric} label="Turnover">${fmt.num(source.turnover_ratio, 2)}×<//>`}
      ${source.average_exposure_pct !== undefined && html`<${Metric} label="Exposure">${fmt.num(source.average_exposure_pct, 0)}%<//>`}
      ${chosen.forward_return_pct !== undefined && chosen.forward_return_pct !== null && html`<${Metric} label="Forward return"><${Change} value=${chosen.forward_return_pct} /><//>`}
      ${source.excess_return_pp !== undefined && source.excess_return_pp !== null && html`<${Metric} label="vs benchmark">${fmt.num(source.excess_return_pp, 1)} pp<//>`}
    </div></div>`}
    <div class="split">
      <div data-tour="cell-holdings" class="panel">${holdings.loading ? html`<${Loading} />` : holdings.error ? html`<${ErrorNote} error=${holdings.error} />`
        : View ? html`<${View} holdings=${holdings.data} />` : html`<p class="muted">Pick a result to see its holdings.</p>`}</div>
      <div class="panel"><h2>Sleeves</h2><${SleevePanel} spec=${record.spec} />
        ${chosen.deployment.version !== record.version && html`<p class="hint">This result came from v${chosen.deployment.version}; sleeves shown are v${record.version}.</p>`}</div>
    </div>
  </div>`;
}

// Configure ------------------------------------------------------------------

const RULES = [
  ["rebalance_every_sessions", "Rebalance every N sessions", "How often scripts decide. 1 means every session.", 1, 1],
  ["max_position_weight", "Largest weight per stock", "A share of the account, from 0 to 1.", 0.01, 0],
  ["min_cash_weight", "Minimum cash", "A share of the account always kept in cash.", 0.01, 0],
  ["max_previous_volume_participation", "Volume cap", "Largest share of the previous session's volume one order may fill. Leave empty for no cap.", 0.001, 0],
];

function SymbolInput({ symbols, onChange, single }) {
  const [draft, setDraft] = useState("");
  const add = (text) => {
    const extra = text.toUpperCase().split(/[\s,;]+/).filter(Boolean);
    if (!extra.length) return;
    const next = [...symbols];
    extra.forEach((symbol) => !next.includes(symbol) && next.push(symbol));
    onChange(single ? next.slice(-1) : next);
    setDraft("");
  };
  return html`<div class="symbol-chips">
    ${symbols.map((symbol) => html`<span class="symbol-chip">${symbol}<button aria-label=${`Remove ${symbol}`}
      onClick=${() => onChange(symbols.filter((item) => item !== symbol))}>×</button></span>`)}
    <input aria-label="Add symbols" placeholder=${symbols.length ? "" : single ? "AAPL" : "AAPL, MSFT, NVDA"} value=${draft}
      onInput=${(event) => setDraft(event.target.value)}
      onKeyDown=${(event) => { if (["Enter", ",", " "].includes(event.key)) { event.preventDefault(); add(draft); }
        else if (event.key === "Backspace" && !draft && symbols.length) onChange(symbols.slice(0, -1)); }}
      onPaste=${(event) => { event.preventDefault(); add(event.clipboardData.getData("text")); }}
      onBlur=${() => add(draft)} />
  </div>`;
}

function ParameterField({ parameter, value, onChange }) {
  const kind = typeof (value ?? parameter.default);
  if (kind === "boolean") {
    return html`<label class="row" style="gap:6px;font-size:13px"><input type="checkbox" checked=${Boolean(value ?? parameter.default)}
      onChange=${(event) => onChange(event.target.checked)} /> ${parameter.name}</label>`;
  }
  return html`<label class="field"><span>${parameter.name}${parameter.required ? " *" : ""}</span>
    <input type=${kind === "number" ? "number" : "text"} value=${value ?? parameter.default ?? ""} step="any"
      onInput=${(event) => onChange(kind === "number" ? (event.target.value === "" ? undefined : Number(event.target.value)) : event.target.value)} /></label>`;
}

// How a Complex cell splits its money between scripts: fixed shares, or an allocator.
function AllocatorPanel({ spec, scripts, update }) {
  const allocators = scripts.flatMap((file) => (file.allocators || []).map((item) => ({ ...item, script: file.key })));
  const current = spec.allocator;
  const value = !current ? "" : current.builtin ? `builtin:${current.class}` : `script:${current.script}:${current.class}`;
  const choose = (choice) => {
    if (!choice) return update({ allocator: null });
    if (choice === "builtin:TrendRegime") return update({ allocator: { builtin: true, class: "TrendRegime",
      parameters: { symbol: spec.symbols[0] || "", window: 50, risk_on: 0, risk_off: Math.min(1, spec.sleeves.length - 1) } } });
    const [, script, name] = choice.split(":");
    update({ allocator: { script, class: name, parameters: {} } });
  };
  const setParameter = (key, next) => update({ allocator: { ...current, parameters: { ...(current.parameters || {}), [key]: next } } });
  const info = current && !current.builtin
    ? allocators.find((item) => item.script === current.script && item.name === current.class) : null;
  const sleeveOptions = spec.sleeves.map((sleeve, index) => html`<option value=${index}>Script ${index + 1} (${sleeve.class || "?"})</option>`);
  return html`<div class="panel stack">
    <div class="row"><h2 style="margin:0">Allocation</h2><${HelpButton} topic="allocator" label="Allocators" /></div>
    <p class="hint" style="margin:0">Fixed shares give each script the share set above. An allocator decides the shares at each close instead, for example switching between scripts by market regime. The Lab compares it with the fixed shares.</p>
    <label class="field"><span>How the money is split</span><select value=${value} onChange=${(event) => choose(event.target.value)}>
      <option value="">Fixed shares</option>
      <option value="builtin:TrendRegime">Trend regime (built in)</option>
      ${allocators.map((item) => html`<option value=${`script:${item.script}:${item.name}`}>${item.name} — ${item.script}</option>`)}</select></label>
    ${current?.builtin && current.class === "TrendRegime" && html`<div class="grid-2">
      <label class="field"><span>Trend symbol</span><select value=${current.parameters?.symbol || ""}
        onChange=${(event) => setParameter("symbol", event.target.value)}>${spec.symbols.map((symbol) => html`<option value=${symbol}>${symbol}</option>`)}</select></label>
      <label class="field"><span>Moving average, sessions</span><input type="number" min="2" step="1" value=${current.parameters?.window ?? 50}
        onInput=${(event) => setParameter("window", Number(event.target.value))} /></label>
      <label class="field"><span>Above the average, all money to</span><select value=${current.parameters?.risk_on ?? 0}
        onChange=${(event) => setParameter("risk_on", Number(event.target.value))}>${sleeveOptions}</select></label>
      <label class="field"><span>Otherwise, all money to</span><select value=${current.parameters?.risk_off ?? 1}
        onChange=${(event) => setParameter("risk_off", Number(event.target.value))}>${sleeveOptions}</select></label></div>`}
    ${info?.parameters.length > 0 && html`<div class="grid-2">${info.parameters.map((parameter) =>
      html`<${ParameterField} parameter=${parameter} value=${current.parameters?.[parameter.name]}
        onChange=${(next) => setParameter(parameter.name, next)} />`)}</div>`}
    ${current && !current.builtin && html`<p class="hint" style="margin:0">An allocator script runs only after you have reviewed it; one an agent wrote is refused until then.</p>`}
  </div>`;
}

const GRANT_DEFAULTS = { mode: "record", max_calls: 300, max_tokens: 300000, max_output_tokens: 2000,
  max_estimated_cost_usd: 1, input_usd_per_million: "", output_usd_per_million: "", min_sessions_between_calls: 1 };
const PRICES = ["input_usd_per_million", "output_usd_per_million"];

// A connection's prices from Quantarium's dated table: zero on this computer, or null when the table does not list it.
function listedPrice(table, connection) {
  if (!table || !connection) return null;
  if ((table.free_presets || []).includes(connection.preset)) return { input_usd_per_million: 0, output_usd_per_million: 0 };
  return table.prices.find((item) => item.preset === connection.preset && item.model === connection.model) || null;
}
const GRANT_FIELDS = [
  ["max_calls", "Most calls, over the tape's life", "1"], ["max_tokens", "Most tokens, over the tape's life", "1"],
  ["max_output_tokens", "Most output tokens per answer", "1"], ["max_estimated_cost_usd", "Most estimated cost, USD", "any"],
  ["input_usd_per_million", "Input price, USD per million tokens", "any"], ["output_usd_per_million", "Output price, USD per million tokens", "any"],
  ["min_sessions_between_calls", "Fewest sessions between calls", "1"]];

// Which reviewed script may call context.ask, through which model, and within what budget.
function ModelAccess({ record, spec, dirty, update, onSaved }) {
  const agents = useAsync(() => get("agents"), []);
  const grant = spec.ask;
  const [editing, setEditing] = useState(false);
  const scriptsOf = [...new Set([...(spec.sleeves || []).map((sleeve) => sleeve.script),
    ...(spec.allocator && !spec.allocator.builtin && spec.allocator.script ? [spec.allocator.script] : [])].filter(Boolean))];
  const connections = (agents.data?.connections || []).filter((item) => item.kind === "direct" && !item.revoked_utc);
  const [form, setForm] = useState(() => ({ ...GRANT_DEFAULTS, ...(grant || {}),
    script: scriptsOf[0] || "", connection_id: grant?.connection_id || "" }));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const chosen = connections.find((item) => item.id === (form.connection_id || connections[0]?.id));
  const listed = listedPrice(agents.data?.prices, chosen);
  const pickConnection = (id) => {
    const price = listedPrice(agents.data?.prices, connections.find((item) => item.id === id));
    setForm({ ...form, connection_id: id, ...Object.fromEntries(PRICES.map((key) => [key, price ? price[key] : ""])) });
  };
  const startEditing = () => {
    if (!grant && listed) setForm({ ...form, ...Object.fromEntries(PRICES.map((key) => [key, listed[key]])) });
    setEditing(true);
  };
  const submit = async () => {
    setBusy(true); setError(null);
    try {
      const { script, connection_id, mode } = form;
      // A blank price is left to the server, which takes it from the table or asks for it.
      await command("cell-ask-grant", { id: record.id, script, connection_id: connection_id || connections[0]?.id, mode,
        ...Object.fromEntries(GRANT_FIELDS.filter(([key]) => form[key] !== "").map(([key]) => [key, Number(form[key])])) });
      setEditing(false); onSaved();
    } catch (problem) { setError(problem); }
    setBusy(false);
  };
  return html`<div class="field stack" style="gap:6px"><span class="row">Model access (<span class="mono">context.ask</span>) <${HelpButton} topic="ask" label="Model access" /></span>
    ${grant && !editing && html`<span class="hint">One reviewed script may ask <span class="mono">${grant.model}</span> in ${grant.mode} mode:
      at most ${grant.max_calls} calls, ${grant.max_tokens} tokens, and $${grant.max_estimated_cost_usd} estimated over the tape's life,
      at least ${grant.min_sessions_between_calls} session${grant.min_sessions_between_calls === 1 ? "" : "s"} apart. Every answer is kept on its tape, shown on the Answers tab,
      and a replay reads the tape instead of calling the model. Editing the script removes the grant's effect until it is granted again.</span>`}
    ${!grant && !editing && html`<span class="hint">Let one of this cell's reviewed scripts ask a language model for decisions, within a budget you set. Every answer is kept, so a replay costs nothing.</span>`}
    ${!editing && html`<div class="row" style="gap:6px">
      <button class="ghost" disabled=${dirty} title=${dirty ? "Save or discard your other changes first" : ""} onClick=${startEditing}>${grant ? "Change model access" : "Give a script model access"}</button>
      ${grant && html`<button class="ghost" onClick=${() => update({ ask: null })}>Remove model access</button>`}</div>`}
    ${editing && html`<div class="stack" style="gap:8px">
      ${agents.data && !agents.data.enabled && html`<p class="notice" style="margin:0">Turn on agent features on the Agents page first; model connections live there.</p>`}
      ${agents.data?.enabled && !connections.length && html`<p class="notice" style="margin:0">Create a Model API connection on the Agents page first.</p>`}
      <div class="grid-2">
        <label class="field"><span>Script that may ask</span><select value=${form.script} onChange=${(event) => setForm({ ...form, script: event.target.value })}>
          ${scriptsOf.map((key) => html`<option value=${key}>${key}</option>`)}</select></label>
        <label class="field"><span>Model connection</span><select value=${form.connection_id || connections[0]?.id || ""} onChange=${(event) => pickConnection(event.target.value)}>
          ${connections.map((item) => html`<option value=${item.id}>${item.name} · ${item.model}${item.training_cutoff ? ` · trained through ${item.training_cutoff}` : ""}</option>`)}</select></label>
        <label class="field"><span>Mode</span><select value=${form.mode} onChange=${(event) => setForm({ ...form, mode: event.target.value })}>
          <option value="record">Record: ask the model, and keep each answer</option>
          <option value="replay">Replay: use kept answers only, never call the model</option></select></label>
        ${GRANT_FIELDS.map(([key, label, step]) => html`<label class="field"><span>${label}</span>
          <input type="number" min="0" step=${step} value=${form[key]} onInput=${(event) => setForm({ ...form, [key]: event.target.value })} /></label>`)}</div>
      <p class="hint" style="margin:0">${listed ? `Prices come from Quantarium's table, checked ${agents.data?.prices?.checked}; change them if your plan differs.` : chosen ? "Quantarium's price table does not list this model: enter its prices." : ""} Quantarium pins the script's current bytes: editing it later voids this access until you grant it again. A script an agent wrote must be reviewed first. Prices are only for the cost estimate; a model on this computer costs nothing.</p>
      <${ErrorNote} error=${error} />
      <div class="row" style="gap:6px"><button class="primary" disabled=${busy || !form.script || !connections.length} onClick=${submit}>${busy ? "Granting…" : "Grant model access"}</button>
        <button class="ghost" onClick=${() => setEditing(false)}>Cancel</button></div></div>`}
  </div>`;
}

// What the model was asked to decide, what it answered, and what that cost.
function Answers({ record }) {
  const tape = useAsync(() => get(`tapes/${record.id}`), [record.id, record.version]);
  if (tape.loading && !tape.data) return html`<${Loading} />`;
  if (tape.error) return html`<${ErrorNote} error=${tape.error} />`;
  const entries = tape.data.entries;
  const failed = entries.filter((entry) => entry.status === "failed").length;
  const tokens = entries.reduce((sum, entry) => sum + (entry.input_tokens || 0) + (entry.output_tokens || 0), 0);
  const cost = entries.reduce((sum, entry) => sum + (entry.estimated_cost_usd || 0), 0);
  const grant = record.spec.ask;
  return html`<div class="panel stack">
    <div class="row between"><div class="row"><h2 style="margin:0">Model answers</h2><${HelpButton} topic="tape" label="Answers" /></div><button class="ghost" onClick=${tape.reload}>Refresh</button></div>
    <p class="hint" style="margin:0">${`${entries.length - failed} answer${entries.length - failed === 1 ? "" : "s"}${failed ? `, ${failed} failed call${failed === 1 ? "" : "s"}` : ""} from ${grant.model}`
      } · ${fmt.int(tokens)} of ${fmt.int(grant.max_tokens)} tokens · calls ${entries.length} of ${grant.max_calls
      } · about $${cost.toFixed(4)} of $${grant.max_estimated_cost_usd}. Failed calls count against the budget too.</p>
    ${entries.length ? html`<div class="table-wrap"><table class="data"><thead><tr><th>Day</th><th>Session</th><th>Status</th><th>Answer</th>
      <th class="num">Tokens</th><th class="num">Cost</th></tr></thead>
      <tbody>${entries.slice(0, 300).map((entry) => html`<tr><td class="mono">${entry.day}</td><td class="num">${entry.session_number}</td>
        <td><span class=${`badge ${entry.status === "failed" ? "down" : "neutral"}`}>${entry.status}</span></td>
        <td class="mono" style="font-size:12px;max-width:420px;overflow-wrap:anywhere">${entry.status === "failed" ? entry.error : JSON.stringify(entry.answer)}</td>
        <td class="num">${fmt.int((entry.input_tokens || 0) + (entry.output_tokens || 0))}</td>
        <td class="num">$${(entry.estimated_cost_usd || 0).toFixed(5)}</td></tr>`)}</tbody></table></div>
      ${entries.length > 300 && html`<p class="hint">Showing the latest 300 of ${entries.length}.</p>`}`
      : html`<p class="muted" style="margin:0">No answers yet. Run the cell, or test it in the Lab, and every answer will appear here.</p>`}
  </div>`;
}

// A cell's decisions come from scripts, a decision file made outside Quantarium, or a forward inbox.
// Opening a forward inbox and registering a decider, without the command line.
function NewSource({ spec, onCreated }) {
  const [kind, setKind] = useState(null);
  const [form, setForm] = useState({});
  const [error, setError] = useState(null);
  const set = (patch) => setForm({ ...form, ...patch });
  const create = async () => {
    setError(null);
    try {
      if (kind === "inbox") await command("decision-inbox-create", { name: form.name || "", symbols: spec.symbols });
      else {
        await command("decider-create", { name: form.name || "", training_cutoff: form.cutoff || null,
          command: [form.program || "", ...(form.args || "").split("\n").map((line) => line.trim()).filter(Boolean)],
          env: (form.env || "").split(",").map((item) => item.trim()).filter(Boolean) });
      }
      setKind(null); setForm({}); onCreated();
    } catch (problem) { setError(problem); }
  };
  if (!kind) return html`<div class="row" style="gap:8px">
    <button class="ghost" onClick=${() => setKind("inbox")} disabled=${!spec.symbols.length}>Open an inbox</button>
    <button class="ghost" onClick=${() => setKind("decider")}>Register a decider</button></div>`;
  return html`<div class="panel stack" style="gap:8px">
    <label class="field"><span>${kind === "inbox" ? "Inbox name" : "Decider name"}</span>
      <input value=${form.name || ""} placeholder="letters, digits, dashes" onInput=${(event) => set({ name: event.target.value })} /></label>
    ${kind === "inbox" && html`<p class="hint">It takes decisions about ${spec.symbols.join(", ")}. Submit them with quantarium decisions submit, or let an agent use submit_decision.</p>`}
    ${kind === "decider" && html`
      <label class="field"><span>Program</span><input class="mono" value=${form.program || ""} placeholder="python" onInput=${(event) => set({ program: event.target.value })} /></label>
      <label class="field"><span>Arguments, one per line</span><textarea class="mono" rows="3" value=${form.args || ""} onInput=${(event) => set({ args: event.target.value })}></textarea></label>
      <label class="field"><span>Environment variables it may receive</span><input class="mono" value=${form.env || ""} placeholder="MY_MODEL_KEY" onInput=${(event) => set({ env: event.target.value })} />
        <span class="hint">Only these, besides basic system variables. Separate them with commas.</span></label>
      <label class="field" style="max-width:260px"><span>Its model's training cutoff (optional)</span><input type="date" value=${form.cutoff || ""} onInput=${(event) => set({ cutoff: event.target.value })} /></label>
      <p class="hint">Quantarium runs this program on your computer with your permissions. Register only programs you trust.</p>`}
    <${ErrorNote} error=${error} />
    <div class="row"><button class="primary" onClick=${create}>${kind === "inbox" ? "Open inbox" : "Register"}</button>
      <button class="ghost" onClick=${() => { setKind(null); setError(null); }}>Cancel</button></div>
  </div>`;
}

function DecisionSource({ spec, decisionFiles, inboxes, deciders, update, onSourcesChanged }) {
  const sleeve = spec.sleeves[0] || {};
  const value = sleeve.decider !== undefined ? `decider:${sleeve.decider}`
    : sleeve.inbox !== undefined ? `inbox:${sleeve.inbox}` : `file:${sleeve.decisions || ""}`;
  const key = sleeve.decisions || "";
  const chosen = sleeve.decisions !== undefined && decisionFiles.find((item) => item.key === key);
  const program = sleeve.decider !== undefined && deciders.find((item) => item.name === sleeve.decider);
  const box = sleeve.inbox !== undefined && inboxes.find((item) => item.name === sleeve.inbox);
  const usable = decisionFiles.filter((item) => !item.error);
  const pick = (choice) => {
    const [kind, ...rest] = choice.split(":");
    update({ sleeves: [{ [{ inbox: "inbox", decider: "decider" }[kind] || "decisions"]: rest.join(":"), allocation: 1 }] });
  };
  return html`<div class="stack" style="gap:8px">
    <label class="field"><span>Decisions from</span><select value=${value} onChange=${(event) => pick(event.target.value)}>
      ${!chosen && !box && !program && html`<option value=${value}>${sleeve.decider || sleeve.inbox || key ? `${sleeve.decider || sleeve.inbox || key} (missing)` : "Choose a file, an inbox, or a decider"}</option>`}
      <optgroup label="Decision files">${decisionFiles.map((item) => html`<option value=${`file:${item.key}`} disabled=${!!item.error}>
        ${item.key}${item.error ? " · invalid" : ` · ${item.decisions} decisions`}</option>`)}</optgroup>
      <optgroup label="Forward inboxes">${inboxes.filter((item) => !item.error).map((item) => html`<option value=${`inbox:${item.name}`}>
        ${item.name} · ${item.on_time} on time${item.late ? `, ${item.late} late` : ""}</option>`)}</optgroup>
      <optgroup label="Deciders">${deciders.map((item) => html`<option value=${`decider:${item.name}`}>${item.name}</option>`)}</optgroup></select></label>
    ${program && html`<p class="hint">The decider ${program.name} runs <span class="mono">${program.command.join(" ")}</span> session by session and sees only data up to each one. ${program.training_cutoff ? `Its model was trained through ${program.training_cutoff}, so the Lab judges it on real history only after that date.` : "No training cutoff is recorded, so the Lab judges it only on synthetic and disguised markets."} Each run records its decisions in decisions/recorded/.</p>`}
    ${box && html`<p class="hint">Forward inbox for ${box.symbols.join(", ")}: ${box.on_time} on-time decision${box.on_time === 1 ? "" : "s"}${box.late ? ` and ${box.late} late, which are left out` : ""}${box.first_date ? `, from ${box.first_date} to ${box.last_date}` : ""}. A decision counts only if it arrived before the next session opened, so it cannot use hindsight; the Lab can judge it in full on real prices.</p>`}
    ${chosen?.error && html`<p class="error-text">${chosen.error}</p>`}
    ${chosen && !chosen.error && html`<p class="hint">${chosen.decisions} decisions from ${chosen.first_date} to ${chosen.last_date} for ${chosen.symbols.join(", ")}${chosen.source?.system ? `, made by ${chosen.source.system}` : ""}. A date without a decision keeps the holdings.</p>`}
    ${!usable.length && html`<p class="muted">No decision files found. Put a <span class="mono">.json</span> or a <span class="mono">date,symbol,weight</span> <span class="mono">.csv</span> file in the <span class="mono">decisions/</span> folder of your workspace.</p>`}
    <${NewSource} spec=${spec} onCreated=${onSourcesChanged || (() => {})} />
    ${!box && !program && html`<p class="hint">Quantarium replays these decisions with its own fills, costs, and rules. A file made after the fact may use hindsight, so the Lab treats its results as exploratory.</p>`}
  </div>`;
}

function Configure({ record, scripts, models, decisionFiles = [], inboxes = [], deciders = [], onSourcesChanged, onSaved }) {
  const [spec, setSpec] = useState(record.spec);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => setSpec(record.spec), [record.version, record.updated_utc]);
  const options = useMemo(() => scripts.flatMap((file) => file.classes.map((item) => ({ ...item, script: file.key }))), [scripts]);
  const dirty = JSON.stringify(spec) !== JSON.stringify(record.spec);
  const update = (patch) => { setSpec({ ...spec, ...patch }); setSaved(false); };
  const setSymbols = (symbols) => update({ symbols,
    initial_positions: Object.fromEntries(Object.entries(spec.initial_positions || {})
      .filter(([symbol]) => symbols.includes(symbol))) });
  const setPosition = (symbol, patch) => {
    const positions = { ...(spec.initial_positions || {}) };
    const next = { ...(positions[symbol] || {}), ...patch };
    if (!next.shares) delete positions[symbol];
    else positions[symbol] = next;
    update({ initial_positions: positions });
  };
  const total = spec.sleeves.reduce((sum, sleeve) => sum + sleeve.allocation, 0);
  const replaying = spec.sleeves.some((sleeve) => "decisions" in sleeve || "inbox" in sleeve || "decider" in sleeve);
  const setSource = (fromFile) => update(fromFile
    ? { sleeves: [{ decisions: decisionFiles.find((item) => !item.error)?.key || "", allocation: 1 }],
        allocator: null, rebalance_every_sessions: 1 }
    : { sleeves: [] });
  const setMode = (mode) => update(mode === "simple"
    ? { mode, symbols: spec.symbols.slice(0, 1),
        initial_positions: Object.fromEntries(Object.entries(spec.initial_positions || {})
          .filter(([symbol]) => symbol === spec.symbols[0])),
        sleeves: spec.sleeves.slice(0, 1).map((sleeve) => ({ ...sleeve, allocation: 1 })) }
    : { mode });
  const setSleeve = (index, patch) => update({ sleeves: spec.sleeves.map((sleeve, at) => (at === index ? { ...sleeve, ...patch } : sleeve)) });
  const addSleeve = () => {
    const first = options[0];
    if (!first) return;
    update({ sleeves: [...spec.sleeves, { script: first.script, class: first.name, allocation: spec.mode === "simple" ? 1 : Math.max(0, Math.round((1 - total) * 100) / 100), parameters: {} }] });
  };
  const save = async () => {
    setSaving(true); setError(null);
    try { await command("cell-save", { id: record.id, spec }); setSaved(true); onSaved(); }
    catch (problem) { setError(problem); }
    setSaving(false);
  };
  const deployed = record.deployments.some((item) => item.version === record.version);
  return html`<div class="split">
    <div class="stack">
      <div data-tour="config-mode" class="panel stack">
        <div class="row"><h2 style="margin:0">Kind of cell</h2></div>
        <div class="choice-grid">
          ${[["simple", "Simple", "One stock, one script."], ["complex", "Complex", "Many stocks and scripts, each script with a share of the money."]].map(([mode, title, text]) =>
            html`<button class=${`choice ${spec.mode === mode ? "on" : ""}`} aria-pressed=${spec.mode === mode} onClick=${() => setMode(mode)}><strong>${title}</strong><small>${text}</small></button>`)}
        </div>
      </div>
      <div data-tour="config-symbols" class="panel stack">
        <h2 style="margin:0">${spec.mode === "simple" ? "Stock" : "Holdings"}</h2>
        <${SymbolInput} symbols=${spec.symbols} single=${spec.mode === "simple"} onChange=${setSymbols} />
        <p class="hint">${spec.mode === "simple" ? "Type a ticker and press Enter." : `${spec.symbols.length} symbols. Paste a list separated by commas or spaces.`}
          ${spec.symbols.length > 30 ? " Over 30 holdings use the aggregate view." : ""}</p>
        <label class="field" style="max-width:220px"><span>Starting cash</span>
          <input type="number" min="1" step="100" value=${spec.initial_cash} onInput=${(event) => update({ initial_cash: Number(event.target.value) })} /></label>
        <details><summary style="cursor:pointer;font-weight:600">Starting holdings and information boundary</summary>
          <p class="hint">Starting holdings are bought at the first session's open from starting cash. Their cost basis may differ from that simulated fill. Keep personal holdings in your local workspace.</p>
          ${spec.symbols.map((symbol) => html`<div class="row" style="gap:10px;align-items:end;margin:8px 0">
            <strong class="mono" style="min-width:70px">${symbol}</strong>
            <label class="field"><span>Shares</span><input type="number" min="0" step="1" value=${spec.initial_positions?.[symbol]?.shares ?? ""}
              onInput=${(event) => setPosition(symbol, { shares: Number(event.target.value) })} /></label>
            <label class="field"><span>Original cost per share</span><input type="number" min="0.01" step="any" value=${spec.initial_positions?.[symbol]?.cost ?? ""}
              disabled=${!spec.initial_positions?.[symbol]?.shares}
              onInput=${(event) => setPosition(symbol, { cost: event.target.value === "" ? undefined : Number(event.target.value) })} /></label>
          </div>`)}
          <label class="field" style="max-width:260px"><span>Information end date</span>
            <input type="date" value=${spec.information_end || ""}
              disabled=${!!spec.model_card}
              onInput=${(event) => update({ information_end: event.target.value || null })} />
            <span class="hint">Without a model card this is self-declared. A saved card supplies its training-label boundary.</span></label>
          <label class="field"><span>Risk model card (optional)</span><select value=${spec.model_card || ""}
            onChange=${(event) => { const key = event.target.value;
              const selected = models.find((item) => item.key === key);
              update({ model_card: key || null,
                information_end: selected?.information_end || spec.information_end }); }}>
            <option value="">No model card</option>
            ${spec.model_card && !models.some((item) => item.key === spec.model_card && !item.error) &&
              html`<option value=${spec.model_card}>${spec.model_card} · unavailable</option>`}
            ${models.filter((item) => !item.error).map((item) => html`<option value=${item.key}>
              ${item.model_id} · labels through ${item.information_end}</option>`)}</select>
            <span class="hint">Train a card with <span class="mono">quantarium ml train-risk</span>. It supplies a risk estimate to scripts and records it; it does not trade for you.</span></label>
          <${ModelAccess} record=${record} spec=${spec} dirty=${dirty} update=${update} onSaved=${onSaved} />
        </details>
      </div>
      <div data-tour="config-scripts" class="panel stack">
        <div class="row between"><div class="row"><h2 style="margin:0">${replaying ? "Decisions" : "Scripts"}</h2>
          ${replaying && html`<${HelpButton} topic="referee" label="Outside decisions" />`}</div>
          ${!replaying && (spec.mode === "complex" || !spec.sleeves.length) && html`<button onClick=${addSleeve} disabled=${!options.length}><${Icon} name="plus" size=${15} /> Add script</button>`}</div>
        <div class="choice-grid">
          ${[[false, "Scripts", "Strategy code in your workspace decides."], [true, "Outside decisions", "A decision file, a forward inbox, or a decider program."]].map(([fromFile, title, text]) =>
            html`<button class=${`choice ${replaying === fromFile ? "on" : ""}`} aria-pressed=${replaying === fromFile} onClick=${() => replaying !== fromFile && setSource(fromFile)}><strong>${title}</strong><small>${text}</small></button>`)}
        </div>
        ${replaying && html`<${DecisionSource} spec=${spec} decisionFiles=${decisionFiles} inboxes=${inboxes} deciders=${deciders}
          update=${update} onSourcesChanged=${onSourcesChanged} />`}
        ${!replaying && !options.length && html`<p class="muted">No scripts found. Put strategy classes in a <span class="mono">strategies/</span> folder of your workspace.</p>`}
        ${!replaying && spec.sleeves.map((sleeve, index) => {
          const info = options.find((item) => item.script === sleeve.script && item.name === sleeve.class);
          return html`<div class="stack" style="gap:8px">
            <div class="sleeve-row">
              <label class="field"><span>Script</span><select value=${`${sleeve.script}|${sleeve.class}`}
                onChange=${(event) => { const [script, name] = event.target.value.split("|"); setSleeve(index, { script, class: name, parameters: {} }); }}>
                ${!info && html`<option value=${`${sleeve.script}|${sleeve.class}`}>${sleeve.class} (missing)</option>`}
                ${options.map((item) => html`<option value=${`${item.script}|${item.name}`}>${item.name} — ${item.script}</option>`)}</select></label>
              <label class="field"><span>Share</span><input type="number" min="0" max="100" step="1" disabled=${spec.mode === "simple"}
                value=${Math.round(sleeve.allocation * 100)} onInput=${(event) => setSleeve(index, { allocation: Number(event.target.value) / 100 })} /></label>
              <button class="ghost" aria-label="Remove script" onClick=${() => update({ sleeves: spec.sleeves.filter((_, at) => at !== index) })}><${Icon} name="trash" size=${16} /></button>
            </div>
            ${info?.doc && html`<p class="hint" style="margin:0 10px">${info.doc}</p>`}
            ${info?.parameters.length > 0 && html`<div class="grid-2" style="margin:0 10px">${info.parameters.map((parameter) =>
              html`<${ParameterField} parameter=${parameter} value=${sleeve.parameters[parameter.name]}
                onChange=${(value) => setSleeve(index, { parameters: { ...sleeve.parameters, [parameter.name]: value } })} />`)}</div>`}
          </div>`;
        })}
        ${!replaying && spec.mode === "complex" && spec.sleeves.length > 0 && html`<p class=${total > 1.0001 ? "error-text" : "hint"}>
          Scripts use ${Math.round(total * 100)}% of the money${total < 0.9999 ? `; ${Math.round((1 - total) * 100)}% is kept as cash` : ""}${total > 1.0001 ? ". Shares must total 100% or less." : "."}</p>`}
      </div>
      ${!replaying && spec.mode === "complex" && spec.sleeves.length > 0 && html`<${AllocatorPanel} spec=${spec} scripts=${scripts} update=${update} />`}
      <details data-tour="config-rules" class="panel"><summary style="cursor:pointer;font-weight:600">Portfolio rules</summary>
        <div class="grid-2" style="margin-top:12px">${RULES.map(([key, label, help, step, min]) => html`<label class="field"><span>${label}</span>
          <input type="number" step=${step} min=${min} value=${spec[key] ?? ""}
            onInput=${(event) => update({ [key]: event.target.value === "" ? (key === "max_previous_volume_participation" ? null : spec[key]) : Number(event.target.value) })} />
          <span class="hint">${help}</span></label>`)}
          ${spec.mode === "complex" && html`<label class="field"><span>Index</span>
            <input value=${spec.index || ""} placeholder="None" onInput=${(event) => update({ index: event.target.value.trim() || undefined })} />
            <span class="hint">On imported research data, hold only this index's members as of each date. Leave empty to trade the symbols above.</span></label>`}</div></details>
    </div>
    <div class="panel stack" style="position:sticky;top:76px">
      <h2 style="margin:0">Summary</h2>
      <div class="row"><${ModeBadge} mode=${spec.mode} /><span class="faint">${spec.symbols.length} holdings · ${replaying ? "a decision file" : `${spec.sleeves.length} scripts`}</span></div>
      <div class="faint" style="font-size:13px">Starting cash ${fmt.money(spec.initial_cash)}</div>
      ${deployed && dirty && html`<div class="notice info">This version has results. Saving creates v${record.version + 1}; earlier results stay with v${record.version}.</div>`}
      ${record.card.problems.length > 0 && !dirty && html`<div class="notice">${record.card.problems.join(" · ")}</div>`}
      <${ErrorNote} error=${error} />
      <div class="row"><button data-tour="config-save" class="primary" onClick=${save} disabled=${!dirty || saving}>${saving ? "Saving…" : "Save"}</button>
        ${dirty && html`<button class="ghost" onClick=${() => setSpec(record.spec)}>Discard</button>`}
        ${saved && !dirty && html`<span class="up" role="status">Saved</span>`}</div>
    </div>
  </div>`;
}

// Runs and timeline ------------------------------------------------------------

function Runs({ results, onOpen }) {
  if (!results.length) return html`<p class="muted">This cell hasn't run yet.</p>`;
  return html`<div class="panel flush"><div class="table-wrap"><table class="data">
    <thead><tr><th>#</th><th>When</th><th>Kind</th><th>Input</th><th>Version</th><th>Evidence</th><th class="num">Return</th><th></th></tr></thead>
    <tbody>${[...results].reverse().map((result) => html`<tr class="clickable" onClick=${() => onOpen(result.index)}>
      <td>${result.index + 1}</td><td>${fmt.date(result.deployment.created_utc)}</td><td>${result.deployment.kind}</td>
      <td class="mono">${result.deployment.label || result.deployment.id}</td><td>v${result.deployment.version}</td>
      <td>${result.evidence && html`<${EvidenceBadge} evidence=${result.evidence} />`}</td>
      <td class="num"><${Change} value=${result.total_return_pct} /></td>
      <td>${result.deployment.kind === "suite" ? html`<a href=${`#/log/suite?config=${encodeURIComponent(result.deployment.config)}`} onClick=${(event) => event.stopPropagation()}>Matrix</a>`
        : result.deployment.kind === "feed" ? html`<a href=${`#/log/feed/${encodeURIComponent(result.deployment.db)}/${encodeURIComponent(result.deployment.id)}`} onClick=${(event) => event.stopPropagation()}>Experiment</a>`
        : html`<a href=${`#/log/run/${encodeURIComponent(result.deployment.db)}/${result.deployment.id}`} onClick=${(event) => event.stopPropagation()}>Run</a>`}</td>
    </tr>`)}</tbody></table></div></div>`;
}

function Timeline({ results, chosen, onPick }) {
  const run = useRunTarget(chosen);
  const [kindFilter, setKindFilter] = useState("all");
  const key = run.target ? JSON.stringify(run.target) : "";
  const timeline = useAsync(() => (run.target ? get("timeline", run.target) : Promise.resolve(null)), [key]);
  if (!results.length) return html`<p class="muted">This cell hasn't run yet.</p>`;
  const entries = timeline.data ? (timeline.data.days || timeline.data.minutes) : [];
  const timeKey = timeline.data?.minutes ? "timestamp" : "date";
  const shown = entries.filter((entry) => kindFilter === "all" || (kindFilter === "orders" ? entry.orders.length
    : kindFilter === "signals" ? entry.signal : entry.other_events.length || entry.corporate_actions.length)).slice(-300).reverse();
  return html`<div class="stack">
    <div class="row between"><div class="row">${[["all", "All"], ["orders", "Orders"], ["signals", "Signals"], ["other", "Actions and events"]].map(([key2, label]) =>
      html`<button class=${`chip ${kindFilter === key2 ? "on" : ""}`} onClick=${() => setKindFilter(key2)}>${label}</button>`)}</div>
      <div class="row">${run.rows?.length > 1 && html`<select aria-label="Scenario" value=${run.row?.scenario} onChange=${(event) => run.setScenario(event.target.value)}>
        ${run.rows.map((row) => html`<option value=${row.scenario}>${row.scenario}</option>`)}</select>`}
        <${ResultPicker} results=${results} chosen=${chosen} onPick=${onPick} /></div></div>
    ${timeline.loading ? html`<${Loading} />` : html`<div data-tour="cell-timeline" class="panel flush"><div class="table-wrap" style="max-height:600px;overflow:auto"><table class="data">
      <thead><tr><th>${timeKey === "date" ? "Date" : "Minute"}</th><th class="num">Equity</th><th>Orders</th><th>Signal</th><th>Other</th></tr></thead>
      <tbody>${shown.map((entry) => html`<tr><td class="mono">${fmt.date(entry[timeKey])}</td>
        <td class="num">${entry.valuation ? fmt.money(entry.valuation.equity) : "—"}</td>
        <td>${entry.orders.map((order) => html`<div class="mono" style="font-size:12px"><span class=${order.side === "buy" ? "up" : "down"}>${order.side}</span>
          ${order.filled_shares}/${order.requested_shares ?? "?"} ${order.symbol}${order.unfilled_reason ? html` <span class="faint">(${order.unfilled_reason})</span>` : ""}</div>`)}</td>
        <td class="mono" style="font-size:12px">${entry.signal ? (entry.signal.target_weights
          ? Object.entries(entry.signal.target_weights).map(([symbol, weight]) => `${symbol} ${Math.round(weight * 100)}%`).join(", ") || "all cash"
          : "hold") : entry.rebalance_skipped ? html`<span class="faint">skipped</span>` : ""}</td>
        <td style="font-size:12px">${[...entry.corporate_actions.map((item) => item.kind), ...entry.other_events.map((item) =>
          item.kind === "model_estimate" ? `risk: ${Object.entries(item.payload.risk_of_drop || {}).map(([symbol, risk]) =>
            `${symbol} ${Math.round(risk * 100)}%`).join(", ")}`
          : item.kind === "model_answer" ? `model: ${JSON.stringify(item.payload.answer).slice(0, 80)}${item.payload.source === "tape" ? " (from tape)" : ""}`
          : item.kind)].join(", ")}</td></tr>`)}</tbody>
    </table></div></div>${entries.length > 300 && html`<p class="hint">Showing the latest 300 of ${entries.length}.</p>`}`}
  </div>`;
}

// Deploy ------------------------------------------------------------------------

// During the first-run tutorial, choices point at the tutorial's own scenario.
function tutorialForm(modes) {
  const names = tutorialActive() ? tutorialNames() : null;
  if (!names) return {};
  const scenario = `scenarios/${names.scenario}`;
  return modes.includes("scenarios") ? { scenarios: [scenario] }
    : { feed_id: names.feed, provider: "scenario", source: scenario, start_date: "2030-01-07", through: TUTORIAL_FEED_THROUGH };
}

export function DeployDialog({ record, onClose, onDone, modes = ["replay", "scenarios", "forward"] }) {
  const [mode, setMode] = useState(tutorialActive() && modes.includes("scenarios") ? "scenarios" : modes[0]);
  const workspace = useAsync(() => get("workspace"), []);
  const [form, setForm] = useState({ data: "", scenarios: [], maps: {}, feedTarget: "new", feed_id: "", provider: "csv", source: "", start_date: "", through: "", slippage_bps: 5, commission_per_order: 1, commission_per_share: 0, commission_minimum: 0, slippage_range_fraction: 0, ...tutorialForm(modes) });
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);
  const symbols = record.spec.symbols;
  const set = (patch) => setForm({ ...form, ...patch });
  const ws = workspace.data;
  const dataFiles = ws?.data_files || [];
  const scenarios = (ws?.scenarios || []).filter((item) => !item.error);
  const feeds = (ws?.configs || []).filter((item) => item.kind === "feed" && item.key.startsWith("configs/generated/"));
  const fits = (available) => symbols.every((symbol) => available.includes(symbol));
  const needsMap = (scenario) => !fits(scenario.symbols) && scenario.symbols.length !== symbols.length;

  const submit = async () => {
    setError(null);
    const costs = Object.fromEntries(["slippage_bps", "commission_per_order", "commission_per_share", "commission_minimum", "slippage_range_fraction"].map((name) => [name, Number(form[name])]));
    let action, params;
    if (mode === "replay") {
      if (!form.data) return setError(new Error("Choose a price file"));
      action = "deploy-static"; params = { cells: [record.id], data: form.data, ...costs };
    } else if (mode === "scenarios") {
      if (!form.scenarios.length) return setError(new Error("Choose at least one scenario"));
      const maps = {};
      for (const key of form.scenarios) {
        const scenario = scenarios.find((item) => item.key === key);
        if (needsMap(scenario)) {
          const mapping = form.maps[key] || {};
          if (!symbols.every((symbol) => mapping[symbol])) return setError(new Error(`Match every holding for ${scenario.name}`));
          if (new Set(symbols.map((symbol) => mapping[symbol])).size !== symbols.length) return setError(new Error(`Use each of ${scenario.name}'s symbols once`));
          maps[key] = Object.fromEntries(Object.entries(mapping).map(([cellSymbol, scenarioSymbol]) => [scenarioSymbol, cellSymbol]));
        }
      }
      action = "deploy-suite"; params = { cells: [record.id], scenarios: form.scenarios, symbol_maps: maps, ...costs };
    } else {
      if (form.feedTarget === "new") {
        if (!/^[A-Za-z0-9_-]{1,64}$/.test(form.feed_id)) return setError(new Error("Name the experiment with letters, digits, dashes, or underscores"));
        if (!form.source || !form.start_date) return setError(new Error("Choose a source and a start date"));
        params = { feed_id: form.feed_id, provider: form.provider, source: form.source, start_date: form.start_date };
        if (form.provider === "scenario" && form.through) params.through = form.through;
      } else {
        params = { feed_id: feeds.find((item) => item.key === form.feedTarget)?.name };
      }
      action = "deploy-feed"; params = { cells: [record.id], ...params, ...costs };
    }
    try { await runJob(action, params, setJob); onDone(); } catch (problem) { setError(problem); setJob(null); }
  };

  const running = job && (job.status === "queued" || job.status === "running");
  const summary = mode === "replay" ? "Replays this cell once over the chosen price file, as fast as possible."
    : mode === "scenarios" ? "Runs this cell on each chosen scenario and compares the results."
    : "Freezes this cell's settings and follows it forward as new sessions are added to the source.";
  return html`<${Dialog} title=${`Run ${record.name}`} onClose=${running ? () => {} : onClose}
    footer=${html`<button data-tour="deploy-cancel" class="ghost" onClick=${onClose} disabled=${running}>Cancel</button>
      <button data-tour="deploy-run" class="primary" onClick=${submit} disabled=${running}>${running ? "Running…" : "Run"}</button>`}>
    <div data-tour="deploy-choices" class="choice-grid">${[["replay", "Replay on data", "Historical prices"], ["scenarios", "Test across scenarios", "Bull, crash, recession…"], ["forward", "Forward test", "A Feed experiment"]].filter(([key]) => modes.includes(key)).map(([key, title, text]) =>
      html`<button class=${`choice ${mode === key ? "on" : ""}`} aria-pressed=${mode === key} onClick=${() => setMode(key)}><strong>${title}</strong><small>${text}</small></button>`)}</div>
    <p class="muted" style="margin:0">${summary}</p>
    ${workspace.loading ? html`<${Loading} />` : mode === "replay" ? html`<div class="stack">
        <label class="field"><span>Price file</span><select value=${form.data} onChange=${(event) => set({ data: event.target.value })}>
          <option value="">Choose…</option>${dataFiles.map((file) => html`<option value=${file.key} disabled=${!fits(file.symbols)}>
            ${file.key} · ${file.start_date} to ${file.end_date}${fits(file.symbols) ? "" : " · missing holdings"}</option>`)}</select></label>
        ${!dataFiles.length && html`<p class="hint">No price files found. Import some with <span class="mono">quantarium import-bars</span> into <span class="mono">data/</span>.</p>`}</div>`
      : mode === "scenarios" ? html`<div class="stack"><div class="check-list">${scenarios.map((scenario) => html`<label>
          <input type="checkbox" checked=${form.scenarios.includes(scenario.key)}
            onChange=${(event) => set({ scenarios: event.target.checked ? [...form.scenarios, scenario.key] : form.scenarios.filter((key) => key !== scenario.key) })} />
          <span>${scenario.name}</span><span class="badge neutral">${scenario.kind}</span><span class="faint">${scenario.tags.join(", ")}</span></label>`)}</div>
          ${form.scenarios.map((key) => scenarios.find((item) => item.key === key)).filter((scenario) => scenario && !fits(scenario.symbols)).map((scenario) =>
            needsMap(scenario) ? html`<div class="panel stack"><strong>${scenario.name}: match its symbols to this cell's</strong>
              ${symbols.map((symbol) => html`<label class="row"><span class="mono" style="width:80px">${symbol}</span>←
                <select value=${form.maps[scenario.key]?.[symbol] || ""} onChange=${(event) => set({ maps: { ...form.maps, [scenario.key]: { ...form.maps[scenario.key], [symbol]: event.target.value } } })}>
                  <option value="">Choose…</option>${scenario.symbols.map((item) => html`<option value=${item}>${item}</option>`)}</select></label>`)}</div>`
            : html`<p class="hint">${scenario.name} uses ${scenario.symbols.join(", ")}; they'll stand in for ${symbols.join(", ")} in sorted order.</p>`)}</div>`
      : html`<div class="stack">
          <label class="field"><span>Experiment</span><select value=${form.feedTarget} onChange=${(event) => set({ feedTarget: event.target.value })}>
            <option value="new">New experiment</option>${feeds.map((item) => html`<option value=${item.key}>Join ${item.name} (${item.cells.length} cells)</option>`)}</select></label>
          ${form.feedTarget === "new" && html`<div class="grid-2">
            <label class="field"><span>Name</span><input value=${form.feed_id} placeholder="my_forward_test" onInput=${(event) => set({ feed_id: event.target.value })} /></label>
            <label class="field"><span>Start date</span><input type="date" value=${form.start_date} onInput=${(event) => set({ start_date: event.target.value })} /></label>
            <label class="field"><span>Source</span><select value=${form.provider} onChange=${(event) => set({ provider: event.target.value, source: "" })}>
              <option value="csv">Price file you keep updating</option><option value="scenario">Scenario (practice run)</option></select></label>
            <label class="field"><span>${form.provider === "csv" ? "Price file" : "Scenario"}</span><select value=${form.source} onChange=${(event) => set({ source: event.target.value })}>
              <option value="">Choose…</option>${(form.provider === "csv" ? dataFiles : scenarios).map((item) => html`<option value=${item.key}>${item.key}</option>`)}</select></label>
            ${form.provider === "scenario" && html`<label class="field"><span>Release sessions through</span>
              <input type="date" value=${form.through} onInput=${(event) => set({ through: event.target.value })} />
              <span class="hint">Optional. Leave empty to release the whole scenario now; Advance releases the rest later.</span></label>`}
          </div>`}
          <div class="notice info">After this, the cell's settings for this experiment are frozen. Later edits create a new version.</div></div>`}
    <details><summary class="muted" style="cursor:pointer">Costs</summary><div class="grid-2" style="margin-top:8px">
      <label class="field"><span>Slippage (basis points)</span><input type="number" min="0" value=${form.slippage_bps} onInput=${(event) => set({ slippage_bps: event.target.value })} /></label>
      <label class="field"><span>Commission per order</span><input type="number" min="0" step="0.5" value=${form.commission_per_order} onInput=${(event) => set({ commission_per_order: event.target.value })} /></label>
      <label class="field"><span>Commission per share</span><input type="number" min="0" step="0.001" value=${form.commission_per_share} onInput=${(event) => set({ commission_per_share: event.target.value })} /></label>
      <label class="field"><span>Minimum commission per order</span><input type="number" min="0" step="0.5" value=${form.commission_minimum} onInput=${(event) => set({ commission_minimum: event.target.value })} /></label>
      <label class="field"><span>Range slippage (share of the previous day's range)</span><input type="number" min="0" max="1" step="0.01" value=${form.slippage_range_fraction} onInput=${(event) => set({ slippage_range_fraction: event.target.value })} /></label></div>
      <p class="hint">A filled order pays the per-order and per-share commissions, but at least the minimum. Range slippage adds a share of the previous session's high-to-low range, so volatile stocks cost more to trade.</p></details>
    ${running && html`<p class="muted" role="status">Job ${job.status}…</p>`}
    <${ErrorNote} error=${error} />
  <//>`;
}
