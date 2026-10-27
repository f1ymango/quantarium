import { html, useState, useMemo, useAsync, Spark, TimeChart, Loading, ErrorNote, Dialog, Icon, seriesColor, fmt, Change } from "../ui.js";
import { get, runJob, command } from "../api.js";
import { HelpButton } from "../tutorial/help.js";
import { tutorialActive, tutorialNames, TUTORIAL_SCENARIO } from "../tutorial/state.js";

const KIND = { historical: "neutral", synthetic: "info", transformed: "warn" };
const SURVIVORSHIP = { survivorship_free: "survivorship-free", survivors_only: "survivors only", unknown: "survivorship unknown" };

export function ScenariosPage({ parts, query }) {
  if (parts[0] === "view") return html`<${ScenarioDetail} scenarioKey=${query.key} />`;
  return html`<${Gallery} />`;
}

function ScenarioCard({ scenario }) {
  const detail = useAsync(() => get("scenario", { key: scenario.key }), [scenario.key]);
  // Imported research data has no price before a listing or after a delisting.
  const first = detail.data ? Object.values(detail.data.closes)[0].map(([, close]) => close).filter((close) => close !== null) : null;
  const change = first ? 100 * (first[first.length - 1] / first[0] - 1) : null;
  return html`<article data-tour=${`scenario-card-${scenario.name}`} class="card scenario-card" style="min-height:0" tabindex="0"
      onClick=${() => (location.hash = `#/scenarios/view?key=${encodeURIComponent(scenario.key)}`)}
      onKeyDown=${(event) => event.key === "Enter" && (location.hash = `#/scenarios/view?key=${encodeURIComponent(scenario.key)}`)}>
    <div class="card-head"><span class="card-name">${scenario.name}</span><span class=${`badge ${KIND[scenario.kind]}`}>${scenario.kind}</span></div>
    <div class="card-portrait">${first ? html`<${Spark} values=${first} height=${64} />` : html`<span class="faint">…</span>`}</div>
    <div class="row between" style="font-size:12.5px"><span class="faint">${scenario.symbols.length} symbols · ${scenario.sessions} sessions</span>
      ${change !== null && html`<${Change} value=${change} />`}</div>
    <div class="row" style="gap:4px">${scenario.holdout && html`<span class="badge warn" title="Hidden from agents">holdout</span>`}
      ${scenario.survivorship && html`<span class=${`badge ${scenario.survivorship === "survivorship_free" ? "up" : "warn"}`} title="The supplier's declaration, recorded on import">${SURVIVORSHIP[scenario.survivorship]}</span>`}
      ${scenario.tags.map((tag) => html`<span class="badge neutral">${tag}</span>`)}</div>
  </article>`;
}

function Gallery() {
  const workspace = useAsync(() => get("workspace"), []);
  const [dialog, setDialog] = useState(null);
  const scenarios = (workspace.data?.scenarios || []).filter((item) => !item.error);
  const broken = (workspace.data?.scenarios || []).filter((item) => item.error);
  return html`<div class="content">
    <div class="row between"><div><div class="row"><h1>Scenarios</h1><${HelpButton} topic="scenario" /></div><p class="muted" style="margin:2px 0 0">Market conditions to test cells against.</p></div>
      <div class="row"><button onClick=${() => setDialog("transform")} disabled=${!scenarios.length}>Transform</button>
        <button data-tour="scenario-generate" class="primary" onClick=${() => setDialog("generate")}><${Icon} name="plus" size=${16} /> Generate</button></div></div>
    ${workspace.loading ? html`<${Loading} />` : html`<div class="card-grid">${scenarios.map((scenario) => html`<${ScenarioCard} key=${scenario.key} scenario=${scenario} />`)}</div>`}
    ${!workspace.loading && !scenarios.length && html`<p class="muted">No scenarios yet. Generate one, slice real data with <span class="mono">quantarium scenario slice</span>, or import research data on the Workspace page.</p>`}
    ${broken.map((item) => html`<div class="notice bad">${item.key}: ${item.error}</div>`)}
    ${dialog === "generate" && html`<${GenerateDialog} onClose=${() => setDialog(null)} onDone=${() => { setDialog(null); workspace.reload(); }} />`}
    ${dialog === "transform" && html`<${TransformDialog} scenarios=${scenarios} onClose=${() => setDialog(null)} onDone=${() => { setDialog(null); workspace.reload(); }} />`}
  </div>`;
}

function HoldoutToggle({ scenarioKey }) {
  const workspace = useAsync(() => get("workspace"), [scenarioKey]);
  const [error, setError] = useState(null);
  const entry = (workspace.data?.scenarios || []).find((item) => item.key === scenarioKey);
  if (!entry || entry.holdout === undefined) return null;
  const toggle = async () => {
    setError(null);
    try { await command("scenario-holdout", { key: scenarioKey, holdout: !entry.holdout }); workspace.reload(); }
    catch (problem) { setError(problem); }
  };
  return html`<div class="panel">
    <div class="row between"><div><h2 style="margin:0 0 4px">Holdout ${entry.holdout && html`<span class="badge warn">on</span>`}</h2>
      <p class="muted" style="margin:0">${entry.holdout
        ? "Agents can't see this scenario, run on it, or read results from it. An agent may ask you, in the Inbox, to test a cell here once per version."
        : "Keep this scenario as a test set agents never see, so a strategy tuned on the others gets an honest check here."}</p></div>
      <button class=${entry.holdout ? "ghost" : "primary"} onClick=${toggle}>${entry.holdout ? "Stop holding out" : "Hold out from agents"}</button></div>
    <${ErrorNote} error=${error} />
  </div>`;
}

function ScenarioDetail({ scenarioKey }) {
  const detail = useAsync(() => get("scenario", { key: scenarioKey }), [scenarioKey]);
  const chart = useMemo(() => {
    if (!detail.data) return null;
    const entries = Object.entries(detail.data.closes);
    return { times: entries[0][1].map(([day]) => day),
      series: entries.slice(0, 8).map(([symbol, rows], index) => ({ label: symbol, values: rows.map(([, close]) => close), color: seriesColor(index) })) };
  }, [detail.data]);
  return html`<div class="content">
    <a href="#/scenarios" class="row faint" style="gap:4px"><${Icon} name="back" size=${16} /> Scenarios</a>
    ${detail.loading ? html`<${Loading} />` : detail.error ? html`<${ErrorNote} error=${detail.error} />` : html`
      <div><div class="row"><h1>${detail.data.name} <span class=${`badge ${KIND[detail.data.kind]}`} style="vertical-align:3px">${detail.data.kind}</span></h1><${HelpButton} topic="scenario" /></div>
        <p class="muted" style="margin:4px 0 0">${detail.data.description || "No description."}</p></div>
      <div class="row">${detail.data.tags.map((tag) => html`<span class="badge neutral">${tag}</span>`)}
        <span class="faint">${detail.data.start_date} to ${detail.data.end_date} · ${detail.data.sessions} sessions</span></div>
      ${detail.data.kind !== "historical" && html`<div class="notice">${detail.data.kind === "synthetic"
        ? "Synthetic: these prices were generated, not observed." : "Transformed: these bars were altered on purpose."}</div>`}
      ${detail.data.survivorship && html`<div class=${`notice ${detail.data.survivorship === "survivorship_free" ? "info" : ""}`}>Imported research data from ${detail.data.provenance.source?.supplier || "an outside supplier"}, declared ${SURVIVORSHIP[detail.data.survivorship]}. ${detail.data.survivorship === "survivorship_free"
        ? "Quantarium records that declaration but cannot verify it. Symbols list and delist during the scenario, as they did."
        : "Companies that failed or were delisted may be missing, which usually flatters a strategy; Lab verdicts on it say so."}</div>`}
      <${HoldoutToggle} scenarioKey=${scenarioKey} />
      <div class="panel"><h2>Closing prices</h2><${TimeChart} times=${chart.times} series=${chart.series} height=${320} />
        ${Object.keys(detail.data.closes).length > 8 && html`<p class="hint">Showing 8 of ${Object.keys(detail.data.closes).length} symbols.</p>`}</div>
      <details class="panel"><summary style="cursor:pointer;font-weight:600">Provenance</summary>
        <pre class="mono" style="white-space:pre-wrap;margin:12px 0 0">${JSON.stringify(detail.data.provenance, null, 2)}</pre></details>`}
  </div>`;
}

function useJob(onDone) {
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);
  const run = async (action, params) => {
    setError(null);
    try { await runJob(action, params, setJob); onDone(); } catch (problem) { setError(problem); setJob(null); }
  };
  return { run, error, setError, running: job && (job.status === "queued" || job.status === "running") };
}

const slug = (text) => text.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 64);

function GenerateDialog({ onClose, onDone }) {
  // The first-run tutorial hands the user a ready-made market to generate.
  const tour = tutorialActive() ? { ...TUTORIAL_SCENARIO, name: tutorialNames().scenario } : null;
  const [form, setForm] = useState(tour ? { name: tour.name, description: tour.description, tags: tour.tags, seed: tour.seed,
    start_date: tour.start_date, symbols: tour.symbols } : { name: "", description: "", tags: "", seed: 7, start_date: "2030-01-07", symbols: "AAA, BBB" });
  const [segments, setSegments] = useState(tour ? tour.segments.map((segment) => ({ ...segment })) : [{ sessions: 252, drift: 10, volatility: 20 }]);
  const job = useJob(onDone);
  const set = (patch) => setForm({ ...form, ...patch });
  const setSegment = (index, patch) => setSegments(segments.map((segment, at) => (at === index ? { ...segment, ...patch } : segment)));
  const submit = () => {
    const name = slug(form.name);
    if (!name) return job.setError(new Error("Give the scenario a name"));
    const symbols = form.symbols.toUpperCase().split(/[\s,;]+/).filter(Boolean);
    if (!symbols.length) return job.setError(new Error("Add at least one symbol"));
    job.run("scenario-generate", { output: `scenarios/${name}`, spec: {
      name, description: form.description, seed: Number(form.seed), start_date: form.start_date, symbols,
      tags: form.tags.split(/[\s,]+/).map(slug).filter(Boolean),
      segments: segments.map((segment) => ({ sessions: Number(segment.sessions), annual_drift: Number(segment.drift) / 100, annual_volatility: Number(segment.volatility) / 100 })),
    } });
  };
  return html`<${Dialog} title="Generate a scenario" onClose=${onClose}
    footer=${html`<button data-tour="scenario-cancel" class="ghost" onClick=${onClose}>Cancel</button><button data-tour="scenario-submit" class="primary" onClick=${submit} disabled=${job.running}>${job.running ? "Generating…" : "Generate"}</button>`}>
    <div data-tour="scenario-form" class="grid-2">
      <label class="field"><span>Name</span><input value=${form.name} placeholder="slow_recession" onInput=${(event) => set({ name: event.target.value })} /></label>
      <label class="field"><span>Tags</span><input value=${form.tags} placeholder="bear, recession" onInput=${(event) => set({ tags: event.target.value })} /></label>
      <label class="field"><span>Symbols</span><input value=${form.symbols} onInput=${(event) => set({ symbols: event.target.value })} /></label>
      <label class="field"><span>Seed</span><input type="number" value=${form.seed} onInput=${(event) => set({ seed: event.target.value })} /></label>
      <label class="field"><span>First session</span><input type="date" value=${form.start_date} onInput=${(event) => set({ start_date: event.target.value })} /></label>
      <label class="field"><span>Description</span><input value=${form.description} onInput=${(event) => set({ description: event.target.value })} /></label>
    </div>
    <div class="stack" style="gap:8px"><strong>Segments, in order</strong>
      ${segments.map((segment, index) => html`<div class="row" style="align-items:end">
        <label class="field"><span>Sessions</span><input type="number" min="1" style="width:90px" value=${segment.sessions} onInput=${(event) => setSegment(index, { sessions: event.target.value })} /></label>
        <label class="field"><span>Annual drift %</span><input type="number" style="width:110px" value=${segment.drift} onInput=${(event) => setSegment(index, { drift: event.target.value })} /></label>
        <label class="field"><span>Annual volatility %</span><input type="number" min="0" style="width:130px" value=${segment.volatility} onInput=${(event) => setSegment(index, { volatility: event.target.value })} /></label>
        ${segments.length > 1 && html`<button class="ghost" aria-label="Remove segment" onClick=${() => setSegments(segments.filter((_, at) => at !== index))}><${Icon} name="trash" size=${16} /></button>`}
      </div>`)}
      <div><button onClick=${() => setSegments([...segments, { sessions: 60, drift: -30, volatility: 35 }])}><${Icon} name="plus" size=${15} /> Add segment</button></div>
      <p class="hint">Sessions are weekdays without holidays. The same seed always gives the same prices.</p></div>
    <${ErrorNote} error=${job.error} />
  <//>`;
}

function TransformDialog({ scenarios, onClose, onDone }) {
  const [form, setForm] = useState({ source: scenarios[0]?.key || "", name: "", op: "shock", start_date: "", sessions: 5, drawdown: 35, recovery_sessions: 30, recovery_fraction: 50, factor: 2, annual: -20 });
  const job = useJob(onDone);
  const set = (patch) => setForm({ ...form, ...patch });
  const parent = scenarios.find((item) => item.key === form.source);
  const submit = () => {
    const name = slug(form.name);
    if (!name) return job.setError(new Error("Give the new scenario a name"));
    const operation = form.op === "shock"
      ? { op: "shock", start_date: form.start_date || parent?.start_date, sessions: Number(form.sessions), drawdown: Number(form.drawdown) / 100,
          recovery_sessions: Number(form.recovery_sessions), recovery_fraction: Number(form.recovery_fraction) / 100 }
      : form.op === "scale_volatility" ? { op: "scale_volatility", factor: Number(form.factor) }
      : { op: "add_drift", annual: Number(form.annual) / 100 };
    job.run("scenario-transform", { source: form.source, output: `scenarios/${name}`, spec: { name, operations: [operation],
      description: `${parent?.name}, ${form.op.replace("_", " ")}` } });
  };
  return html`<${Dialog} title="Transform a scenario" onClose=${onClose}
    footer=${html`<button class="ghost" onClick=${onClose}>Cancel</button><button class="primary" onClick=${submit} disabled=${job.running}>${job.running ? "Working…" : "Create"}</button>`}>
    <div class="grid-2">
      <label class="field"><span>Start from</span><select value=${form.source} onChange=${(event) => set({ source: event.target.value })}>
        ${scenarios.map((item) => html`<option value=${item.key}>${item.name}</option>`)}</select></label>
      <label class="field"><span>New name</span><input value=${form.name} placeholder="bull_with_crash" onInput=${(event) => set({ name: event.target.value })} /></label>
      <label class="field"><span>Change</span><select value=${form.op} onChange=${(event) => set({ op: event.target.value })}>
        <option value="shock">Crash</option><option value="scale_volatility">Scale volatility</option><option value="add_drift">Add drift</option></select></label>
    </div>
    ${form.op === "shock" ? html`<div class="grid-2">
        <label class="field"><span>Start date</span><input type="date" min=${parent?.start_date} max=${parent?.end_date} value=${form.start_date || parent?.start_date}
          onInput=${(event) => set({ start_date: event.target.value })} /><span class="hint">Must be a session in the scenario.</span></label>
        <label class="field"><span>Drop %</span><input type="number" min="1" max="99" value=${form.drawdown} onInput=${(event) => set({ drawdown: event.target.value })} /></label>
        <label class="field"><span>Over sessions</span><input type="number" min="1" value=${form.sessions} onInput=${(event) => set({ sessions: event.target.value })} /></label>
        <label class="field"><span>Recover % of the drop</span><input type="number" min="0" max="100" value=${form.recovery_fraction} onInput=${(event) => set({ recovery_fraction: event.target.value })} /></label>
        <label class="field"><span>Recovery sessions</span><input type="number" min="0" value=${form.recovery_sessions} onInput=${(event) => set({ recovery_sessions: event.target.value })} /></label></div>`
      : form.op === "scale_volatility" ? html`<label class="field" style="max-width:220px"><span>Multiply volatility by</span>
          <input type="number" min="0" step="0.1" value=${form.factor} onInput=${(event) => set({ factor: event.target.value })} /></label>`
      : html`<label class="field" style="max-width:220px"><span>Annual drift to add, %</span>
          <input type="number" value=${form.annual} onInput=${(event) => set({ annual: event.target.value })} /></label>`}
    <${ErrorNote} error=${job.error} />
  <//>`;
}
