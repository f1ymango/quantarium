// Feed, the arena: cells being followed forward, grouped by experiment.
import { html, useState, useAsync, Spark, ModeBadge, Change, fmt, Icon, Loading, ErrorNote, navigate } from "../ui.js";
import { get, runJob } from "../api.js";
import { PickAndDeploy } from "./pick.js";
import { HelpButton } from "../tutorial/help.js";

function ArenaCard({ cell, experiment, busy, onRetire }) {
  const practice = cell.input === "scenario";
  const forward = !practice && cell.forward_sessions > 0;
  const headline = forward ? cell.forward_return_pct : cell.total_return_pct;
  const open = () => (cell.cell_id ? navigate(`cells/${cell.cell_id}`)
    : navigate(`log/feed/${encodeURIComponent(experiment.db)}/${encodeURIComponent(experiment.id)}`));
  const statusClass = { active: "results", failed: "failed", retired: "draft" }[cell.status] || "configured";
  return html`<article class=${`card ${cell.mode || ""} ${cell.status !== "active" ? "draft" : ""}`} style="min-height:0" tabindex="0"
      onClick=${open} onKeyDown=${(event) => event.key === "Enter" && open()} aria-label=${`${cell.name} in ${experiment.id}, ${cell.status}`}>
    <div class="card-head">
      <span class=${`status-dot status-${statusClass}`} title=${cell.status}></span>
      <span class="card-name">${cell.name}</span>
      ${cell.mode && html`<${ModeBadge} mode=${cell.mode} />`}
    </div>
    <div class="row between" style="align-items:baseline">
      <span class="faint" style="font-size:12px">${forward ? "Forward return" : practice ? "Practice return" : "Return (backfill only)"}</span>
      <${Change} value=${headline} className="stat-value" />
    </div>
    <div class="card-portrait">${cell.spark?.length > 1 ? html`<${Spark} values=${cell.spark} height=${64} />` : html`<span class="faint">No sessions yet</span>`}</div>
    <div class="stat-sheet">
      ${!practice && html`<div class="stat-line" style="grid-template-columns:1fr auto"><span class="label">Forward sessions</span><span class="num">${fmt.int(cell.forward_sessions)}</span></div>
      <div class="stat-line" style="grid-template-columns:1fr auto"><span class="label">Backfill sessions</span><span class="num">${fmt.int(cell.backfill_sessions)}</span></div>`}
      ${practice && html`<div class="stat-line" style="grid-template-columns:1fr auto"><span class="label">Sessions</span><span class="num">${fmt.int(cell.sessions)}</span></div>`}
      <div class="stat-line" style="grid-template-columns:1fr auto"><span class="label">Drawdown</span><span class="num">${fmt.num(cell.max_drawdown_pct, 1)}%</span></div>
      <div class="stat-line" style="grid-template-columns:1fr auto"><span class="label">Sharpe</span><span class="num">${fmt.num(cell.sharpe_ratio_rf0, 2)}</span></div>
    </div>
    <div class="card-foot" style="align-items:center">
      <span class=${`badge ${cell.status === "active" ? "up" : cell.status === "failed" ? "down" : "neutral"}`} title=${cell.error || ""}>${cell.status}</span>
      <span>last ${cell.last_date || "—"}</span><span style="flex:1"></span>
      ${cell.status === "active" && html`<button class="ghost" style="padding:2px 8px;font-size:12px" disabled=${busy}
        onClick=${(event) => { event.stopPropagation(); onRetire(cell); }}>Retire</button>`}
    </div>
  </article>`;
}

export function ArenaPage() {
  const arena = useAsync(() => get("arena"), []);
  const [importing, setImporting] = useState(false);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const [note, setNote] = useState(null);
  const experiments = arena.data || [];
  const runnable = experiments.filter((item) => item.config && item.status === "active");

  const advance = async (list) => {
    setBusy("advance"); setError(null); setNote(null);
    try {
      let total = 0;
      for (const experiment of list) total += (await runJob("feed-batch", { config: experiment.config })).days_processed_this_call;
      setNote(`Processed ${total} new session${total === 1 ? "" : "s"} across ${list.length} experiment${list.length === 1 ? "" : "s"}.`);
      arena.reload();
    } catch (problem) { setError(problem); }
    setBusy(null);
  };
  const retire = async (experiment, cell) => {
    if (!confirm(`Retire ${cell.name} from ${experiment.id}? It stops taking new sessions and cannot be restarted.`)) return;
    setBusy(cell.name); setError(null);
    try { await runJob("retire-cell", { kind: "feed", db: experiment.db, id: experiment.id, cell: cell.name }); arena.reload(); }
    catch (problem) { setError(problem); }
    setBusy(null);
  };

  return html`<div class="content">
    <div class="row between">
      <div><div class="row"><h1>Feed</h1><${HelpButton} topic="feed-batch" /></div><p class="muted" style="margin:2px 0 0">The arena: your cells followed forward, one new session at a time.</p></div>
      <div class="row">
        ${runnable.length > 0 && html`<button onClick=${() => advance(runnable)} disabled=${busy}>${busy === "advance" ? "Advancing…" : "Advance all"}</button>`}
        <button data-tour="feed-import" class="primary" onClick=${() => setImporting(true)}><${Icon} name="plus" size=${16} /> Import from Static</button>
      </div>
    </div>
    <${ErrorNote} error=${error || arena.error} />
    ${note && html`<div class="notice info" role="status">${note}</div>`}
    ${arena.loading && !arena.data ? html`<${Loading} label="Gathering the arena…" />` : !experiments.length
      ? html`<div class="panel empty"><div class="empty-art"><${Icon} name="feed" size=${36} /></div>
          <h2 style="margin:0">The arena is empty</h2>
          <p class="muted" style="margin:0;max-width:440px">Bring a cell you've tested in Static into a forward test. It keeps the same settings and meets sessions it has never seen.</p>
          <button data-tour="feed-import-empty" class="primary" onClick=${() => setImporting(true)}>Import from Static</button></div>`
      : experiments.map((experiment) => html`<section data-tour=${`feed-experiment-${experiment.id}`} class="stack">
          <div class="row between">
            <div class="row"><h2 style="margin:0">${experiment.id}</h2>
              <span class=${`badge ${experiment.provider === "scenario" ? "info" : "neutral"}`}>${experiment.provider === "scenario" ? "practice · scenario" : experiment.provider}</span>
              <span class="faint">since ${experiment.start_date} · last session ${experiment.last_date || "—"}</span></div>
            <div class="row">
              <a href=${`#/log/feed/${encodeURIComponent(experiment.db)}/${encodeURIComponent(experiment.id)}`}>Details</a>
              ${experiment.config && experiment.status === "active" && html`<button data-tour=${`feed-advance-${experiment.id}`} onClick=${() => advance([experiment])} disabled=${busy}>Advance</button>`}
            </div>
          </div>
          ${experiment.error ? html`<${ErrorNote} error=${experiment.error} />` : html`<div class="card-grid">
            ${experiment.cells.filter((cell) => !cell.archived).map((cell) => html`<${ArenaCard} key=${cell.name} cell=${cell} experiment=${experiment}
              busy=${Boolean(busy)} onRetire=${(target) => retire(experiment, target)} />`)}</div>`}
        </section>`)}
    <div data-tour="feed-live" class="panel row" style="gap:14px;align-items:flex-start">
      <div class="empty-art" style="width:44px;height:44px;border-radius:12px;flex:none"><${Icon} name="feed" size=${20} /></div>
      <div class="stack" style="gap:4px"><strong>Live Feed</strong>
        <span class="muted">Live Feed follows completed one-minute bars during a single market session, from a data provider such as Alpaca.
          It starts from the command line with <span class="mono">quantarium live-feed</span>; its results appear in the <a href="#/log?view=feed">Log</a>.
          Feed Batch, above, advances one whole day at a time.</span></div>
    </div>
    ${importing && html`<${PickAndDeploy} title="Import a cell from Static" scope="static" modes=${["forward"]}
      hint="Choose a cell to follow forward. Its current settings are frozen for this experiment."
      onClose=${() => setImporting(false)} onDone=${() => { setImporting(false); arena.reload(); }} />`}
  </div>`;
}
