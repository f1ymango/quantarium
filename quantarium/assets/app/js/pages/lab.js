import { html, useState, useAsync, Loading, ErrorNote, fmt } from "../ui.js";
import { get, command, runJob } from "../api.js";
import { HelpButton } from "../tutorial/help.js";
import { tutorialActive, tutorialNames, tutorialCellId } from "../tutorial/state.js";

const MEMORY = { clean: "Clean", contaminated: "May remember", unknown: "Unknown" };
const PARTS = { ask: "Without the language model", risk: "Without the risk card", allocator: "With fixed shares instead of its allocator" };

const VERDICTS = {
  supported_within_tested_scope: "Supported within tested scope",
  not_supported: "Not supported",
  inconclusive: "Inconclusive",
  unknown: "Unknown",
};

const pts = (value) => value === null || value === undefined ? "—"
  : `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value).toFixed(2)} pts`;
const PART_NAMES = { ask: "Language model", risk: "Risk card", allocator: "Allocator" };

function Registration({ cells, scenarios, onRegistered, initialCell, initialIdea = "", initialName = "", initialScenario = null, tutorial = false }) {
  const [form, setForm] = useState({ idea: initialIdea, name: initialName, cell: initialCell || "", scenarios: initialScenario ? [initialScenario] : [],
    excess: 0, drawdown: "", sessions: 60, slippage: 0, commission: 0, perShare: 0, minimum: 0, range: 0, disguised: false });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const update = (patch) => setForm({ ...form, ...patch });
  const register = async () => {
    setBusy(true); setError(null);
    try {
      const result = await command("lab-register", {
        idea: form.idea.trim(), name: form.name.trim(), cells: [form.cell],
        scenarios: form.scenarios,
        pass_rule: { min_mean_excess_return_pp: Number(form.excess),
          max_drawdown_pct: form.drawdown === "" ? null : Number(form.drawdown),
          min_sessions: Number(form.sessions) },
        slippage_bps: Number(form.slippage), commission_per_order: Number(form.commission),
        commission_per_share: Number(form.perShare), commission_minimum: Number(form.minimum),
        slippage_range_fraction: Number(form.range),
        disguised_replay: form.disguised,
      });
      onRegistered(result.id);
    } catch (problem) { setError(problem); }
    setBusy(false);
  };
  return html`<details class="panel" style="margin-bottom:20px" open=${!!initialCell} data-tour=${tutorial ? "lab-register" : undefined}>
    <summary style="cursor:pointer;font-weight:600">Pre-register an experiment</summary>
    <div class="stack" style="margin-top:16px">
      <p class="hint">Freeze one cell, its scripts, the scenarios, two automatic controls, costs, and a pass rule before results are shown. Running consumes these scenario data for this idea.</p>
      <div class="grid-2">
        <label class="field"><span>Idea ID</span><input value=${form.idea} placeholder="trend_idea" readOnly=${tutorial}
          onInput=${(event) => update({ idea: event.target.value })} />
          <span class="hint">${tutorial ? "Set by the tutorial, which follows its attempts by this ID." : "Reuse this ID for later variants so the ledger can count attempts and used data."}</span></label>
        <label class="field"><span>Experiment name</span><input value=${form.name} placeholder="Trend across two markets"
          onInput=${(event) => update({ name: event.target.value })} /></label>
        <label class="field"><span>Candidate cell</span><select value=${form.cell}
          onChange=${(event) => update({ cell: event.target.value })}>
          <option value="">Choose a cell</option>
          ${cells.map((cell) => html`<option value=${cell.id}>${cell.name}</option>`)}</select></label>
        <label class="field"><span>Minimum mean excess return, percentage points</span>
          <input type="number" min="0" step="any" value=${form.excess}
            onInput=${(event) => update({ excess: event.target.value })} /></label>
        <label class="field"><span>Maximum drawdown, % (optional)</span>
          <input type="number" min="0" max="100" step="any" value=${form.drawdown}
            onInput=${(event) => update({ drawdown: event.target.value })} /></label>
        <label class="field"><span>Minimum sessions per scenario</span>
          <input type="number" min="20" step="1" value=${form.sessions}
            onInput=${(event) => update({ sessions: event.target.value })} /></label>
        <label class="field"><span>Slippage, basis points</span>
          <input type="number" min="0" step="any" value=${form.slippage}
            onInput=${(event) => update({ slippage: event.target.value })} /></label>
        <label class="field"><span>Commission per order, USD</span>
          <input type="number" min="0" step="any" value=${form.commission}
            onInput=${(event) => update({ commission: event.target.value })} /></label>
        <label class="field"><span>Commission per share, USD</span>
          <input type="number" min="0" step="any" value=${form.perShare}
            onInput=${(event) => update({ perShare: event.target.value })} /></label>
        <label class="field"><span>Minimum commission per order, USD</span>
          <input type="number" min="0" step="any" value=${form.minimum}
            onInput=${(event) => update({ minimum: event.target.value })} /></label>
        <label class="field"><span>Range slippage, share of the previous day's range</span>
          <input type="number" min="0" max="1" step="any" value=${form.range}
            onInput=${(event) => update({ range: event.target.value })} /></label>
      </div>
      <div><strong>Scenarios</strong><p class="hint">Choose at least one. Holdouts use their separate one-time test.</p>
        ${scenarios.map((scenario) => html`<label class="row" style="gap:8px;margin:6px 0">
          <input type="checkbox" checked=${form.scenarios.includes(scenario.key)}
            onChange=${(event) => update({ scenarios: event.target.checked
              ? [...form.scenarios, scenario.key]
              : form.scenarios.filter((key) => key !== scenario.key) })} />
          <span>${scenario.name} <span class="faint">${scenario.kind} · ${scenario.sessions} sessions</span></span>
        </label>`)}</div>
      <label class="row" style="gap:8px;align-items:flex-start">
        <input type="checkbox" checked=${form.disguised} onChange=${(event) => update({ disguised: event.target.checked })} />
        <span><strong>Disguised replay</strong> <span class="hint">Also run the cell on copies of its real-history scenarios with invented symbols, future dates, and rescaled prices. The gap shows whether it depends on recognising the market. It is a diagnostic and does not change the verdict; a cell that asks a model makes new calls for the copies.</span></span></label>
      <${ErrorNote} error=${error} />
      <div><button class="primary" disabled=${busy || !form.idea || !form.name || !form.cell || !form.scenarios.length}
        onClick=${register}>${busy ? "Registering…" : "Freeze registration"}</button></div>
    </div>
  </details>`;
}

// Save the experiment's evidence report: one self-contained page, built by the server.
async function downloadReport(id) {
  const { filename, html: page } = await get(`lab-report/${id}`);
  const link = document.createElement("a");
  link.download = filename;
  link.href = URL.createObjectURL(new Blob([page], { type: "text/html" }));
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

// How the candidate's Sharpe ratio holds up once the number of attempts at the idea is allowed for.
function Statistics({ statistics }) {
  const chance = (value) => `${Math.round(value * 100)}%`;
  const threshold = statistics.dsr_threshold;
  const pbo = statistics.pbo || {};
  return html`<h3 class="row" style="margin:0">Allowing for the number of attempts <${HelpButton} topic="trials" label="Attempts and luck" /></h3>
    <p class="hint" style="margin:0">${statistics.attempts} attempt${statistics.attempts === 1 ? "" : "s"} at this idea. The deflated Sharpe ratio is the chance the candidate's edge is real after allowing for them; supported needs at least ${chance(threshold)} in every scenario.</p>
    <div class="table-wrap"><table class="data"><thead><tr><th>Scenario</th><th class="num">Sharpe, annual</th>
      <th class="num">Probabilistic Sharpe</th><th class="num">Deflated Sharpe</th></tr></thead>
      <tbody>${statistics.rows.map((row) => html`<tr><td>${row.scenario}</td>
        <td class="num">${row.sharpe_annual === undefined ? "—" : fmt.num(row.sharpe_annual)}</td>
        <td class="num">${row.psr === null || row.psr === undefined ? "—" : chance(row.psr)}</td>
        <td class=${`num ${row.dsr !== null && row.dsr !== undefined && row.dsr < threshold ? "warn-text" : ""}`}>${row.dsr === null || row.dsr === undefined ? (row.reason || "—") : chance(row.dsr)}</td></tr>`)}</tbody></table></div>
    <p class="hint" style="margin:0">Probability of backtest overfitting: ${pbo.pbo === null || pbo.pbo === undefined ? `not measured (${pbo.reason})` : chance(pbo.pbo)}.</p>`;
}

function Experiment({ record, onRan }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [live, setLive] = useState(null);
  const spec = record.spec;
  const verdict = record.result?.verdict;
  const run = async () => {
    if (!confirm("Run this frozen experiment? Its scenario data will count as used for this idea.")) return;
    setBusy(true); setError(null);
    let last = 0;
    // While it runs, look in on the ledger every two seconds for its stage and model calls.
    const watch = () => { if (Date.now() - last > 2000) { last = Date.now(); get(`lab/${record.id}`).then(setLive).catch(() => {}); } };
    try { await runJob("lab-run", { id: record.id }, watch); onRan(); }
    catch (problem) { setError(problem); onRan(); }
    setLive(null); setBusy(false);
  };
  const progress = live?.progress;
  return html`<div class="stack">
    <div class="panel stack" data-tour="lab-experiment">
      <div class="row between"><div><h2 style="margin:0">${spec.name}</h2>
        <p class="faint" style="margin:4px 0">Idea ${spec.idea} · attempt ${record.attempt_number} · ${spec.candidate.name}</p></div>
        <span class="badge neutral">${record.status}</span></div>
      <p class="hint">Frozen ${fmt.date(record.created_utc)} · ${spec.scenarios.map((item) => item.name).join(", ")}</p>
      <p class="hint">Pass rule: mean return at least ${fmt.num(spec.pass_rule.min_mean_excess_return_pp)} percentage points above the stronger control, ${spec.pass_rule.min_sessions} sessions per scenario${spec.pass_rule.max_drawdown_pct === null ? "" : `, drawdown at most ${fmt.pct(spec.pass_rule.max_drawdown_pct)}`}.</p>
      ${record.status === "registered" && html`<div><button class="primary" disabled=${busy} onClick=${run} data-tour="lab-run">${busy ? "Running…" : "Run frozen experiment"}</button></div>`}
      ${record.status !== "registered" && html`<div><button class="ghost" onClick=${() => downloadReport(record.id).catch(setError)}>Download evidence report</button>
        <span class="hint"> One page with the verdict, its statistics, and everything it rests on. Check it with quantarium lab verify-report.</span></div>`}
      ${busy && html`<p class="hint" role="status" style="margin:0">${progress ? `${progress.stage}: ${progress.scenario} (${progress.step} of ${progress.of})` : "Starting…"}${live?.model_calls !== undefined ? ` · ${live.model_calls} model call${live.model_calls === 1 ? "" : "s"} so far` : ""}</p>`}
      ${record.error && html`<p class="notice">${record.error}</p>`}
      <${ErrorNote} error=${error} />
    </div>
    ${verdict && html`<div class="panel stack">
      <h2 style="margin:0" data-tour="lab-verdict">${VERDICTS[verdict.status] || verdict.status}</h2>
      <p class="hint">This is a simulation verdict for the registered scenarios and costs, not a claim about future returns.</p>
      ${verdict.scope.model && html`<p class="hint">Asks ${verdict.scope.model}; training cutoff ${verdict.scope.model_training_cutoff || "not recorded"}. A real-history scenario is clean only if it begins after that date; synthetic markets are always clean.</p>`}
      <p class="hint">Universe: ${verdict.scope.index ? `members of ${verdict.scope.index} as of each date, from ` : ""}${verdict.scope.universe.join(", ")} · slippage ${fmt.num(verdict.scope.slippage_bps)} bps · commission ${fmt.num(verdict.scope.commission_per_order)} USD/order${Object.entries(verdict.scope.costs || {}).map(([name, value]) => ` · ${name.replaceAll("_", " ")} ${fmt.num(value)}`).join("")} · information boundary: ${verdict.scope.information_end || "not supplied"} (${verdict.scope.information_boundary_status.replaceAll("_", " ")}).</p>
      <div class="table-wrap"><table class="data"><thead><tr><th>Scenario</th><th>Kind</th>
        <th>Period</th><th class="num">Candidate</th><th class="num">Buy and hold</th><th class="num">Equal weight</th><th class="num">Excess over best</th>
        <th class="num">Drawdown</th><th class="num">Sessions</th>${verdict.scope.model && html`<th>Model memory</th>`}</tr></thead>
        <tbody>${verdict.rows.map((row) => html`<tr><td>${row.scenario}</td>
          <td>${row.kind}${row.lineage && row.lineage !== row.kind ? html` <span class="faint">from ${row.lineage}</span>` : ""}${row.survivorship ? html` <span class="faint">· ${row.survivorship.replaceAll("_", " ")}</span>` : ""}</td>
          <td>${row.start_date}–${row.end_date}</td>
          <td class="num">${fmt.pct(row.candidate_return_pct)}</td>
          <td class="num">${fmt.pct(row.controls.find((control) => control.name.startsWith("lab_buy_hold"))?.return_pct)}</td>
          <td class="num">${fmt.pct(row.controls.find((control) => control.name.startsWith("lab_equal_weight"))?.return_pct)}</td>
          <td class="num">${fmt.pct(row.excess_return_pp)}</td>
          <td class="num">${fmt.pct(row.drawdown_pct)}</td>
          <td class="num">${fmt.int(row.sessions)}</td>
          ${verdict.scope.model && html`<td>${MEMORY[row.model_contamination] || "—"}</td>`}</tr>`)}</tbody></table></div>
      ${verdict.statistics && html`<${Statistics} statistics=${verdict.statistics} />`}
      ${verdict.rows.some((row) => row.ablations?.length) && html`<h3 style="margin:0">What each part adds</h3>
        <p class="hint">The same cell with one part switched off. Without the language model, a sleeve keeps its last target whenever it would have asked.</p>
        <div class="table-wrap"><table class="data"><thead><tr><th>Scenario</th><th>Variant</th><th class="num">Its return</th><th class="num">Part's contribution</th></tr></thead>
          <tbody>${verdict.rows.flatMap((row) => (row.ablations || []).map((entry) => html`<tr><td>${row.scenario}</td>
            <td>${PARTS[entry.part] || entry.part}</td><td class="num">${fmt.pct(entry.return_pct)}</td>
            <td class="num">${fmt.pct(entry.contribution_pp)}</td></tr>`))}</tbody></table></div>`}
      ${verdict.disguise && html`<h3 style="margin:0">Disguised replay</h3>
        ${verdict.disguise.rows.length > 0 && html`<div class="table-wrap"><table class="data"><thead><tr><th>Scenario</th><th class="num">Original</th><th class="num">Disguised</th><th class="num">Gap</th><th>Status</th></tr></thead>
          <tbody>${verdict.disguise.rows.map((row) => html`<tr><td>${row.scenario}</td>
            <td class="num">${fmt.pct(row.original_return_pct)}</td><td class="num">${fmt.pct(row.disguised_return_pct)}</td>
            <td class="num">${fmt.pct(row.gap_pp)}</td><td>${row.status}${row.error ? html` <span class="faint">${row.error}</span>` : ""}</td></tr>`)}</tbody></table></div>`}
        <ul class="hint" style="margin:0">${verdict.disguise.caveats.map((caveat) => html`<li>${caveat}</li>`)}</ul>`}
      <h3 style="margin:0">What limits this result</h3>
      <ul class="hint" style="margin:0">${verdict.caveats.map((caveat) => html`<li data-tour=${caveat.includes("already used related scenario data") ? "lab-reused-data" : undefined}>${caveat}</li>`)}</ul>
    </div>`}
  </div>`;
}

// One model-driven cell: its parts, and what its latest Lab experiment found.
function SupercellCard({ item, onOpen, onTest }) {
  const { parts, evidence } = item;
  const ask = parts.ask;
  const used = ask && ask.max_calls ? Math.min(1, (ask.answers + ask.failed) / ask.max_calls) : 0;
  return html`<div class="panel stack supercell">
    <div class="row between" style="align-items:flex-start"><div>
      <h3 style="margin:0">${item.name} <span class="faint" style="font-weight:400">v${item.version}</span></h3>
      <div class="row" style="gap:4px;margin-top:6px;flex-wrap:wrap">
        ${parts.risk_card && html`<span class="badge neutral">Risk card</span>`}
        ${ask && html`<span class="badge neutral">Asks ${ask.model}</span>`}
        ${parts.allocator && html`<span class="badge neutral">${parts.allocator.builtin ? "Built-in" : "Script"} allocator: ${parts.allocator.class}</span>`}
      </div></div>
      <span class=${`badge ${evidence?.status === "supported_within_tested_scope" ? "up" : evidence?.status === "not_supported" ? "down" : "neutral"}`}>
        ${evidence ? VERDICTS[evidence.status] || evidence.status : "Not tested"}</span></div>
    ${ask && html`<div class="stack" style="gap:4px">
      <p class="hint" style="margin:0">Training cutoff: ${ask.training_cutoff || html`<span class="warn-text">not recorded</span>`} · ${ask.mode} mode
        ${!ask.connection_active && html` · <span class="warn-text">its connection is missing or revoked</span>`}</p>
      <p class="hint" style="margin:0">Tape: ${ask.answers} answer${ask.answers === 1 ? "" : "s"}${ask.failed ? `, ${ask.failed} failed` : ""} · ${fmt.int(ask.tokens)} tokens · about $${ask.estimated_cost_usd.toFixed(4)} of $${ask.max_estimated_cost_usd}</p>
      <div class="meter" title="Calls used of the grant's budget"><span style=${`width:${Math.round(used * 100)}%`}></span></div></div>`}
    ${evidence ? html`<div class="stack" style="gap:4px">
      <p style="margin:0"><strong>${evidence.name}</strong> <span class="faint">· attempt ${evidence.attempt} · ${evidence.scenarios} scenario${evidence.scenarios === 1 ? "" : "s"}</span></p>
      ${!evidence.current_version && html`<p class="notice" style="margin:0">This evidence is for v${evidence.for_version}; the current version has not been tested.</p>`}
      ${evidence.model_contamination && html`<p class="hint" style="margin:0">Model memory: ${evidence.model_contamination.clean} clean, ${evidence.model_contamination.contaminated} may remember, ${evidence.model_contamination.unknown} unknown</p>`}
      ${Object.entries(evidence.contributions_pp || {}).map(([part, value]) => html`<p class="hint" style="margin:0">${PART_NAMES[part] || part} adds ${pts(value)} on average</p>`)}
      ${evidence.disguise_gap_pp !== null && evidence.disguise_gap_pp !== undefined && html`<p class="hint" style="margin:0">Disguised replay gap: ${pts(evidence.disguise_gap_pp)}</p>`}</div>`
      : html`<p class="hint" style="margin:0">No Lab experiment yet. A supercell's results mean little until it is compared with simple controls on frozen data.</p>`}
    ${item.problems.length > 0 && html`<p class="notice" style="margin:0">${item.problems[0]}</p>`}
    <div class="row" style="gap:6px;flex-wrap:wrap">
      ${evidence && html`<button class="ghost" onClick=${() => onOpen(evidence.experiment_id)}>Open experiment</button>`}
      <button class="ghost" disabled=${item.problems.length > 0} onClick=${() => onTest(item.id)}>Test in the Lab</button>
      <a class="ghost button" href=${`#/cells/${item.id}?tab=configure`}>Configure</a></div>
  </div>`;
}

function Supercells({ onOpen, onTest }) {
  const listing = useAsync(() => get("supercells"), []);
  if (listing.loading && !listing.data) return html`<${Loading} />`;
  if (listing.error) return html`<${ErrorNote} error=${listing.error} />`;
  const items = listing.data || [];
  return html`<div class="stack">
    <div class="row"><strong>Supercells</strong><${HelpButton} topic="supercells" label="Supercells" /></div>
    <p class="muted" style="margin:0">A supercell is a cell whose decisions use a model: a risk card's estimates, a language model's answers, or an allocator that splits money between scripts. Each card shows what the Lab has found so far, not a promise of returns.</p>
    ${items.length ? html`<div class="supercell-grid">${items.map((item) => html`<${SupercellCard} key=${item.id} item=${item} onOpen=${onOpen} onTest=${onTest} />`)}</div>`
      : html`<div class="panel stack"><p style="margin:0">No supercells yet. To make one, open a cell's Configure tab and:</p>
          <ul class="hint" style="margin:0"><li>choose a risk model card in Advanced settings;</li>
            <li>give a reviewed script model access, so it can call <span class="mono">context.ask</span>;</li>
            <li>or, in a Complex cell, choose an allocator in the Allocation panel.</li></ul></div>`}
  </div>`;
}

export function LabPage() {
  const ledger = useAsync(() => get("lab"), []);
  const cells = useAsync(() => get("cells"), []);
  const workspace = useAsync(() => get("workspace"), []);
  const [selected, setSelected] = useState(null);
  const [view, setView] = useState("experiments");
  const [prefill, setPrefill] = useState(null);
  const records = ledger.data || [];
  const ready = Boolean(ledger.data && cells.data && workspace.data);
  const active = records.find((item) => item.id === selected) || records[0];
  const inTutorial = tutorialActive();
  const names = inTutorial ? tutorialNames() : null;
  const tutorialCell = inTutorial ? tutorialCellId() : null;
  const tutorialIdea = "tutorial_first_cell";
  const tutorialAttempt = records.filter((item) => item.spec.idea === tutorialIdea).length;
  const tutorialScenario = inTutorial ? workspace.data?.scenarios.find((item) => item.name === names?.scenario) : null;
  return html`<div class="content stack">
    <div><div class="row"><h1>Lab</h1><${HelpButton} topic="lab" /></div>
      <p class="muted" style="margin:2px 0 0">Freeze a test before seeing its result. Compare a cell with simple controls, and keep every attempt in the ledger.</p></div>
    <div class="row" style="gap:6px">
      ${[["experiments", "Experiments"], ["supercells", "Supercells"]].map(([key, label]) => html`<button
        class=${view === key ? "primary" : "ghost"} onClick=${() => setView(key)}>${label}</button>`)}</div>
    ${view === "supercells" && html`<${Supercells}
      onOpen=${(id) => { setSelected(id); setView("experiments"); }}
      onTest=${(id) => { setPrefill(id); setView("experiments"); }} />`}
    ${view === "experiments" && html`<${ErrorNote} error=${ledger.error || cells.error || workspace.error} />`}
    ${view === "experiments" && ready && html`<${Registration} key=${inTutorial ? `tutorial-${tutorialAttempt}` : prefill || "new"} initialCell=${tutorialCell || prefill}
      initialIdea=${inTutorial ? tutorialIdea : ""} initialName=${inTutorial ? `${names.cell} · attempt ${tutorialAttempt + 1}` : ""}
      initialScenario=${tutorialScenario?.key || null} tutorial=${inTutorial}
      cells=${cells.data.filter((cell) => !cell.problems.length)}
      scenarios=${workspace.data.scenarios.filter((scenario) => !scenario.error && !scenario.holdout)}
      onRegistered=${(id) => { setSelected(id); setPrefill(null); ledger.reload(); }} />`}
    ${view === "experiments" && !ready && !ledger.error && !cells.error && !workspace.error && html`<${Loading} label="Loading Lab…" />`}
    ${view === "experiments" && ready && !records.length && html`<div class="panel"><p class="muted" style="margin:0">No Lab experiments yet.</p></div>`}
    ${view === "experiments" && records.length > 0 && html`<div class="split"><div class="panel stack">
      <h2 style="margin:0">Ledger</h2>
      ${records.map((item) => html`<button class=${item.id === active?.id ? "choice on" : "choice"}
        style="text-align:left" onClick=${() => setSelected(item.id)}>
        <strong>${item.spec.name}</strong><small>${item.spec.idea} · attempt ${item.attempt_number} · ${VERDICTS[item.result?.verdict?.status] || item.status}</small>
      </button>`)}</div>
      <${Experiment} key=${active.id} record=${active} onRan=${ledger.reload} /></div>`}
  </div>`;
}
