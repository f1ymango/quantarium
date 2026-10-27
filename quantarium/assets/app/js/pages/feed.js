import { html, useState, useMemo, useAsync, Loading, ErrorNote, fmt, Icon, Change } from "../ui.js";
import { get, runJob } from "../api.js";
import { CellTable, EquityChart } from "./static.js";
import { HelpButton } from "../tutorial/help.js";

// Log entries for Feed: Feed Batch and Live Feed experiments.
export function FeedList() {
  const workspace = useAsync(() => get("workspace"), []);
  if (workspace.loading) return html`<${Loading} />`;
  if (workspace.error) return html`<${ErrorNote} error=${workspace.error} />`;
  const all = workspace.data.databases.flatMap((db) => db.experiments.map((item) => ({ ...item, db: db.key })));
  const feeds = all.filter((item) => item.kind === "feed");
  const lives = all.filter((item) => item.kind === "live");
  return html`<div class="stack" style="gap:20px">
    <div class="panel flush"><div style="padding:16px 18px 0"><h2>Feed Batch</h2></div>
      ${feeds.length ? html`<div class="table-wrap"><table class="data"><thead><tr><th>Experiment</th><th>Source</th><th>Started</th><th>Last session</th><th>Cells</th><th>Status</th></tr></thead>
        <tbody>${feeds.map((feed) => html`<tr class="clickable" onClick=${() => (location.hash = `#/log/feed/${encodeURIComponent(feed.db)}/${encodeURIComponent(feed.id)}`)}>
          <td>${feed.id}</td><td><span class="badge neutral">${feed.provider}</span></td><td>${feed.start_date}</td><td>${feed.last_date || "—"}</td>
          <td>${Object.entries(feed.cell_statuses).map(([status, count]) => `${count} ${status}`).join(", ")}</td>
          <td><span class=${`badge ${feed.status === "active" ? "up" : "neutral"}`}>${feed.status}</span></td></tr>`)}</tbody></table></div>`
        : html`<p class="muted" style="padding:0 18px 16px">No Feed experiments yet. Bring a cell into the <a href="#/feed">arena</a>.</p>`}</div>
    <div class="panel flush"><div style="padding:16px 18px 0"><h2>Live Feed</h2><p class="hint" style="margin-top:-6px">Run from the command line; results are shown here.</p></div>
      ${lives.length ? html`<div class="table-wrap"><table class="data"><thead><tr><th>Experiment</th><th>Session</th><th>Cells</th><th>Status</th></tr></thead>
        <tbody>${lives.map((live) => html`<tr class="clickable" onClick=${() => (location.hash = `#/log/live/${encodeURIComponent(live.db)}/${live.id}`)}>
          <td>${live.id}</td><td>${fmt.date(live.start_at)} – ${fmt.date(live.stop_at).slice(11)}</td><td>${live.cells}</td>
          <td><span class=${`badge ${live.status === "complete" ? "up" : live.status === "halted" ? "warn" : "neutral"}`} title=${live.stop_reason || ""}>${live.status}</span></td></tr>`)}</tbody></table></div>`
        : html`<p class="muted" style="padding:0 18px 16px">No Live Feed results.</p>`}</div>
  </div>`;
}

export function FeedDetail({ db, id }) {
  const analysis = useAsync(() => get("analysis", { db, kind: "feed", id }), [db, id]);
  const workspace = useAsync(() => get("workspace"), []);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const [note, setNote] = useState(null);
  const config = (workspace.data?.configs || []).find((item) => item.kind === "feed" && item.name === id);
  // Shade the historical backfill so the forward part stands out.
  const bands = useMemo(() => {
    if (!analysis.data) return [];
    const times = [...new Set(analysis.data.curve.map((row) => row.date))].sort();
    const backfill = new Set(analysis.data.curve.filter((row) => row.phase === "backfill").map((row) => row.date));
    const last = times.reduce((at, time, index) => (backfill.has(time) ? index : at), -1);
    return last > 0 ? [[0, last]] : [];
  }, [analysis.data]);
  const act = async (label, action, params) => {
    setBusy(label); setError(null); setNote(null);
    try {
      const result = await runJob(action, params);
      if (action === "feed-batch") setNote(`Processed ${result.days_processed_this_call} new session${result.days_processed_this_call === 1 ? "" : "s"}.`);
      analysis.reload();
    } catch (problem) { setError(problem); }
    setBusy(null);
  };
  const scenarioInput = analysis.data?.cells.some((cell) => cell.input === "scenario");
  return html`<div class="content">
    <a href="#/log?view=feed" class="row faint" style="gap:4px"><${Icon} name="back" size=${16} /> Log</a>
    <div class="row between"><div class="row"><h1>${id}</h1><${HelpButton} topic="feed-batch" /></div>
      <button class="primary" disabled=${!config || busy} title=${config ? "Fetch and process new sessions" : "No config found for this experiment"}
        onClick=${() => act("advance", "feed-batch", { config: config.key })}>${busy === "advance" ? "Advancing…" : "Advance"}</button></div>
    ${scenarioInput && html`<div class="notice info">This experiment reads a scenario. Its sessions are practice, not a forward test.</div>`}
    <${ErrorNote} error=${error || analysis.error} />
    ${note && html`<div class="notice info" role="status">${note}</div>`}
    ${analysis.loading && !analysis.data ? html`<${Loading} />` : analysis.data && html`
      <div class="panel"><div class="row between"><h2 style="margin:0">Equity</h2>
        ${bands.length > 0 && html`<span class="legend"><span><i style="background:var(--surface-sunk);border:1px solid var(--border)"></i>Backfill (history before the config was frozen)</span></span>`}</div>
        <${EquityChart} analysis=${analysis.data} bands=${bands} /></div>
      <div class="panel flush"><${CellTable} cells=${analysis.data.cells} showForward=${!scenarioInput}
        actions=${(cell) => cell.status === "active" && html`<button class="ghost" disabled=${busy}
          onClick=${() => confirm(`Retire ${cell.name}? It stops taking new sessions and cannot be restarted.`) && act(cell.name, "retire-cell", { kind: "feed", db, id, cell: cell.name })}>Retire</button>`} /></div>`}
  </div>`;
}

export function LiveDetail({ db, id }) {
  const analysis = useAsync(() => get("analysis", { db, kind: "live", id }), [db, id]);
  return html`<div class="content">
    <a href="#/log?view=feed" class="row faint" style="gap:4px"><${Icon} name="back" size=${16} /> Log</a>
    <div class="row"><h1>${id} <span class="badge neutral" style="vertical-align:3px">Live</span></h1><${HelpButton} topic="live-feed" /></div>
    ${analysis.loading ? html`<${Loading} />` : analysis.error ? html`<${ErrorNote} error=${analysis.error} />` : html`
      <div class="panel"><h2>Equity by minute</h2><${EquityChart} analysis=${analysis.data} /></div>
      <div class="panel flush"><${CellTable} cells=${analysis.data.cells} /></div>`}
  </div>`;
}
