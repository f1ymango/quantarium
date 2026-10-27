import { html, useState, useMemo, useAsync, TimeChart, Change, Loading, ErrorNote, fmt, seriesColor, Icon } from "../ui.js";
import { get } from "../api.js";
import { HelpButton } from "../tutorial/help.js";

const METRICS = [
  ["total_return_pct", "Return", true], ["max_drawdown_pct", "Max drawdown", false],
  ["annualized_volatility_pct", "Volatility", false], ["sharpe_ratio_rf0", "Sharpe", true],
  ["excess_return_pp", "vs benchmark (pp)", true],
];

// Log entries for Static: suites and individual runs.
export function StaticList() {
  const workspace = useAsync(() => get("workspace"), []);
  if (workspace.loading) return html`<${Loading} />`;
  if (workspace.error) return html`<${ErrorNote} error=${workspace.error} />`;
  const suites = workspace.data.configs.filter((item) => item.kind === "suite");
  const runs = workspace.data.databases.flatMap((db) => db.experiments.filter((item) => item.kind === "static")
    .map((item) => ({ ...item, db: db.key }))).sort((a, b) => b.created_utc.localeCompare(a.created_utc));
  return html`<div class="stack" style="gap:20px">
    <div class="panel"><h2>Suites</h2>
      ${suites.length ? html`<div class="table-wrap"><table class="data"><thead><tr><th>Suite</th><th>Cells</th><th>Config</th></tr></thead>
        <tbody>${suites.map((suite) => html`<tr class="clickable" onClick=${() => (location.hash = `#/log/suite?config=${encodeURIComponent(suite.key)}`)}>
          <td>${suite.name}</td><td>${suite.cells.join(", ")}</td><td class="mono faint">${suite.key}</td></tr>`)}</tbody></table></div>`
        : html`<p class="muted">No suites yet. Run a cell across scenarios to make one.</p>`}</div>
    <div class="panel flush"><div style="padding:16px 18px 0"><h2>Runs</h2></div>
      ${runs.length ? html`<div class="table-wrap"><table class="data"><thead><tr><th>When</th><th>Status</th><th>Cells</th><th>Scenario</th><th>Database</th></tr></thead>
        <tbody>${runs.slice(0, 200).map((run) => html`<tr class="clickable" onClick=${() => (location.hash = `#/log/run/${encodeURIComponent(run.db)}/${run.id}`)}>
          <td>${fmt.date(run.created_utc)}</td><td><span class=${`badge ${run.status === "completed" ? "up" : run.status === "failed" ? "down" : "neutral"}`}>${run.status}</span></td>
          <td class="num">${run.cells}</td><td>${run.scenario ? html`${run.scenario} <span class="faint">(${run.scenario_kind})</span>` : html`<span class="faint">—</span>`}</td>
          <td class="mono faint">${run.db}</td></tr>`)}</tbody></table></div>`
        : html`<p class="muted" style="padding:0 18px 16px">No Static runs yet.</p>`}</div>
  </div>`;
}

function CellTable({ cells, showForward = false, actions }) {
  return html`<div class="table-wrap"><table class="data"><thead><tr><th>Cell</th><th>Status</th>
    <th class="num">Return</th>${showForward && html`<th class="num">Forward</th>`}<th class="num">Max drawdown</th><th class="num">Volatility</th>
    <th class="num">Sharpe</th><th class="num">Turnover</th><th class="num">Sessions</th>${actions && html`<th></th>`}</tr></thead>
    <tbody>${cells.map((cell) => html`<tr><td>${cell.name}</td>
      <td><span class=${`badge ${cell.status === "failed" ? "down" : cell.status === "retired" || cell.status === "stopped" ? "neutral" : "up"}`}
        title=${cell.error || ""}>${cell.status}</span></td>
      <td class="num"><${Change} value=${cell.total_return_pct} /></td>
      ${showForward && html`<td class="num">${cell.forward_sessions ? html`<${Change} value=${cell.forward_return_pct} />` : html`<span class="faint">none yet</span>`}</td>`}
      <td class="num">${fmt.num(cell.max_drawdown_pct, 1)}%</td><td class="num">${fmt.num(cell.annualized_volatility_pct, 1)}%</td>
      <td class="num">${fmt.num(cell.sharpe_ratio_rf0, 2)}</td><td class="num">${fmt.num(cell.turnover_ratio, 2)}×</td>
      <td class="num">${fmt.int(cell.sessions ?? cell.minutes)}</td>${actions && html`<td>${actions(cell)}</td>`}</tr>`)}</tbody></table></div>`;
}

export function EquityChart({ analysis, bands = [] }) {
  const timeKey = analysis.kind === "live" ? "timestamp" : "date";
  const { times, series } = useMemo(() => {
    const allTimes = [...new Set(analysis.curve.map((row) => row[timeKey]))].sort();
    const index = new Map(allTimes.map((time, at) => [time, at]));
    return { times: allTimes, series: analysis.cells.map((cell, at) => {
      const values = new Array(allTimes.length).fill(null);
      analysis.curve.filter((row) => row.cell === cell.name).forEach((row) => { values[index.get(row[timeKey])] = row.index_100; });
      return { label: cell.name, values, color: seriesColor(at) };
    }) };
  }, [analysis]);
  return html`<${TimeChart} times=${times} series=${series} height=${300} yLabel="Index (start = 100)" bands=${bands} />`;
}

export function RunDetail({ db, id }) {
  const analysis = useAsync(() => get("analysis", { db, kind: "static", id }), [db, id]);
  return html`<div class="content">
    <a href="#/log?view=static" class="row faint" style="gap:4px"><${Icon} name="back" size=${16} /> Log</a>
    <div class="row"><h1>Run <span class="mono" style="font-size:15px">${id.slice(0, 12)}</span></h1><${HelpButton} topic="metrics" /></div>
    ${analysis.loading ? html`<${Loading} />` : analysis.error ? html`<${ErrorNote} error=${analysis.error} />` : html`
      <div class="panel"><h2>Equity</h2><${EquityChart} analysis=${analysis.data} /></div>
      <div class="panel flush"><${CellTable} cells=${analysis.data.cells} /></div>`}
  </div>`;
}

export function SuiteMatrix({ config }) {
  const [metric, setMetric] = useState("total_return_pct");
  const report = useAsync(() => get("suite-report", { config }), [config]);
  if (report.loading) return html`<div class="content"><${Loading} label="Comparing scenarios…" /></div>`;
  if (report.error) return html`<div class="content"><a href="#/log?view=static">Log</a><${ErrorNote} error=${report.error} /></div>`;
  const data = report.data;
  const [, label, higherIsBetter] = METRICS.find(([key]) => key === metric);
  const cells = [...new Set(data.matrix.map((row) => row.cell))];
  const scenarios = data.scenarios;
  const lookup = new Map(data.matrix.map((row) => [`${row.cell}|${row.scenario}`, row]));
  const values = data.matrix.map((row) => row[metric]).filter((value) => typeof value === "number");
  const extent = Math.max(1e-9, ...values.map((value) => Math.abs(value)));
  const shade = (value) => {
    if (typeof value !== "number") return "";
    const goodness = (higherIsBetter ? value : -value) / extent;
    const strength = Math.min(1, Math.abs(goodness)) * 0.55;
    return `background:color-mix(in srgb, ${goodness >= 0 ? "var(--up)" : "var(--down)"} ${Math.round(strength * 100)}%, transparent)`;
  };
  const show = (value) => (typeof value !== "number" ? "—" : metric === "sharpe_ratio_rf0" ? fmt.num(value, 2)
    : metric.endsWith("_pp") ? fmt.num(value, 1) : metric === "total_return_pct" ? fmt.pct(value) : `${fmt.num(value, 1)}%`);
  const tags = [...new Set(data.by_tag.map((row) => row.tag))];
  return html`<div class="content">
    <a href="#/log?view=static" class="row faint" style="gap:4px"><${Icon} name="back" size=${16} /> Log</a>
    <div class="row between"><div><div class="row"><h1>${data.suite}</h1><${HelpButton} topic="suite" /></div><p class="muted" style="margin:2px 0 0">${cells.length} cells × ${scenarios.length} scenarios${data.benchmark ? ` · benchmark ${data.benchmark}` : ""}</p></div>
      <select aria-label="Metric" value=${metric} onChange=${(event) => setMetric(event.target.value)}>${METRICS.map(([key, text]) => html`<option value=${key}>${text}</option>`)}</select></div>
    ${data.caveats.map((caveat) => html`<div class="notice">${caveat}</div>`)}
    <div class="panel flush"><div class="table-wrap"><table class="data matrix">
      <thead><tr><th>Cell</th>${scenarios.map((scenario) => html`<th class="num" title=${scenario.description}>${scenario.name}<div class="faint" style="font-weight:400">${scenario.kind}</div></th>`)}</tr></thead>
      <tbody>${cells.map((cell) => html`<tr><td>${cell}</td>${scenarios.map((scenario) => {
        const row = lookup.get(`${cell}|${scenario.name}`);
        return html`<td class="cellv" style=${shade(row?.[metric])} title=${row?.status || ""}>${show(row?.[metric])}</td>`;
      })}</tr>`)}</tbody></table></div></div>
    <p class="hint">${label}: green is better, red is worse, relative to the largest value shown.</p>
    ${tags.length > 0 && html`<div class="panel flush"><div style="padding:16px 18px 0"><h2>By tag</h2></div><div class="table-wrap"><table class="data">
      <thead><tr><th>Tag</th>${cells.map((cell) => html`<th class="num">${cell}</th>`)}</tr></thead>
      <tbody>${tags.map((tag) => html`<tr><td><span class="badge neutral">${tag}</span></td>${cells.map((cell) => {
        const row = data.by_tag.find((item) => item.tag === tag && item.cell === cell);
        return html`<td class="num" title=${`Worst ${fmt.pct(row?.worst_total_return_pct)} · mean max drawdown ${fmt.num(row?.mean_max_drawdown_pct, 1)}%`}>
          <${Change} value=${row?.mean_total_return_pct} /></td>`;
      })}</tr>`)}</tbody></table></div><p class="hint" style="padding:0 18px 12px">Mean total return across scenarios with each tag.</p></div>`}
  </div>`;
}

export { CellTable };
