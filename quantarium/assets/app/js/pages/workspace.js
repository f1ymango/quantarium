import { html, useAsync, useState, Loading, ErrorNote, fmt } from "../ui.js";
import { get, command, runJob } from "../api.js";
import { HelpButton } from "../tutorial/help.js";

export function WorkspacePage() {
  const overview = useAsync(() => get("workspace"), []);
  const scripts = useAsync(() => get("scripts"), []);
  const health = useAsync(() => get("health"), []);
  if (overview.loading) return html`<div class="content"><${Loading} /></div>`;
  if (overview.error) return html`<div class="content"><${ErrorNote} error=${overview.error} /></div>`;
  const data = overview.data;
  return html`<div class="content">
    <div><div class="row"><h1>Workspace</h1><${HelpButton} topic="workspace" /></div><p class="muted mono" style="margin:4px 0 0">${data.root}</p></div>
    <div class="grid-2">
      <div class="panel"><h2>Server</h2><p style="margin:0">${location.origin} · Quantarium ${health.data?.version || ""} · API ${health.data?.api || ""}</p>
        <p class="hint">Only this computer can reach it. Running a cell executes its scripts as local Python.</p></div>
      <div class="panel"><h2>Folders scanned</h2><table class="data"><tbody>${Object.entries(data.folders).map(([kind, folders]) =>
        html`<tr><td>${kind}</td><td class="mono">${folders.join(", ")}</td></tr>`)}</tbody></table>
        <p class="hint">Change them with a <span class="mono">quantarium-workspace.json</span> file.</p></div>
    </div>
    <div data-tour="workspace-prices" class="panel flush"><div style="padding:16px 18px 0"><h2>Price files</h2></div>
      ${data.data_files.length ? html`<div class="table-wrap"><table class="data"><thead><tr><th>File</th><th>Symbols</th><th>Range</th><th class="num">Sessions</th><th>Manifest</th></tr></thead>
        <tbody>${data.data_files.map((file) => html`<tr><td class="mono">${file.key}</td><td>${file.symbols.slice(0, 8).join(", ")}${file.symbols.length > 8 ? ` +${file.symbols.length - 8}` : ""}</td>
          <td>${file.start_date} – ${file.end_date}</td><td class="num">${fmt.int(file.sessions)}</td><td>${file.manifest ? html`<span class="badge up">imported</span>` : html`<span class="faint">—</span>`}</td></tr>`)}</tbody></table></div>`
        : html`<p class="muted" style="padding:0 18px 16px">No price files. Use <span class="mono">quantarium import-bars data/tape.csv AAPL=AAPL.csv …</span></p>`}</div>
    <${OutsideData} data=${data} />
    <div class="panel flush"><div style="padding:16px 18px 0"><h2>Scripts</h2></div>
      ${scripts.data?.length ? html`<div class="table-wrap"><table class="data"><thead><tr><th>File</th><th>Strategy classes</th></tr></thead>
        <tbody>${scripts.data.map((file) => html`<tr><td class="mono">${file.key}</td><td>${file.classes.map((item) => html`<span class="badge neutral" style="margin:0 4px 4px 0" title=${item.doc}>${item.name}</span>`)}</td></tr>`)}</tbody></table></div>`
        : html`<p class="muted" style="padding:0 18px 16px">No strategy scripts found.</p>`}</div>
    <div class="panel flush"><div style="padding:16px 18px 0"><h2>Databases</h2></div><div class="table-wrap"><table class="data">
      <thead><tr><th>File</th><th class="num">Experiments</th><th class="num">Size</th></tr></thead>
      <tbody>${data.databases.map((db) => html`<tr><td class="mono">${db.key}</td><td class="num">${db.experiments.length}</td>
        <td class="num">${(db.size_bytes / 1048576).toFixed(1)} MB</td></tr>`)}</tbody></table></div></div>
  </div>`;
}

const DECLARED = { survivorship_free: "survivorship-free", survivors_only: "survivors only", unknown: "unknown" };

// Research data and trades the user brought in: imported or converted only by the user's hand.
function OutsideData({ data }) {
  const [busy, setBusy] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const folders = data.data_folders || [], trades = data.trade_files || [];
  const act = async (key, work) => {
    setBusy(key); setError(null); setResult(null);
    // No reload: nothing listed here changes, and reloading would unmount this panel and its message.
    try { setResult(await work()); } catch (problem) { setError(problem); }
    setBusy(null);
  };
  const importFolder = (folder) => act(folder.key, async () => {
    const name = folder.key.split("/").pop().toLowerCase().replace(/[^a-z0-9_-]+/g, "_").slice(0, 64);
    const imported = await runJob("data-import", { folder: folder.key, name });
    return { text: `Imported ${imported.key}: ${imported.symbols.length} symbols, ${imported.sessions} sessions.`, warnings: imported.warnings };
  });
  const convert = (file) => act(file.key, async () => {
    const made = await command("trades-convert", { trades: file.key });
    return { text: `Wrote ${made.output}: ${made.trades} trades as ${made.orders} orders on ${made.decisions} sessions. Choose it on a cell's Configure tab as a decision file.`,
      warnings: [...made.warnings, ...made.limits] };
  });
  return html`<div class="panel stack">
    <h2 style="margin:0">Research data and trades</h2>
    <p class="hint">Quantarium supplies no market data. Put research data in its <a href="#/learn/own-data">documented format</a> (a folder with <span class="mono">source.json</span>) or a trades CSV (<span class="mono">date,symbol,side,shares,price</span>) under <span class="mono">data/</span>. Only you can import them; agents can read the results.</p>
    ${folders.length ? html`<div class="table-wrap"><table class="data"><thead><tr><th>Data folder</th><th>Declared</th><th>Files</th><th></th></tr></thead>
      <tbody>${folders.map((folder) => html`<tr><td class="mono">${folder.key}</td><td>${DECLARED[folder.survivorship] || folder.survivorship}</td>
        <td class="faint">${folder.files.join(", ")}</td>
        <td><button onClick=${() => importFolder(folder)} disabled=${busy !== null}>${busy === folder.key ? "Importing…" : "Import as a scenario"}</button></td></tr>`)}</tbody></table></div>`
      : html`<p class="muted" style="margin:0">No research data folders.</p>`}
    ${trades.length ? html`<div class="table-wrap"><table class="data"><thead><tr><th>Trades file</th><th></th></tr></thead>
      <tbody>${trades.map((file) => html`<tr><td class="mono">${file.key}</td>
        <td><button onClick=${() => convert(file)} disabled=${busy !== null}>${busy === file.key ? "Converting…" : "Make a decision file"}</button></td></tr>`)}</tbody></table></div>`
      : html`<p class="muted" style="margin:0">No trades files.</p>`}
    ${result && html`<div class="notice info">${result.text}${result.warnings?.length ? html`<ul style="margin:6px 0 0">${result.warnings.map((item) => html`<li>${item}</li>`)}</ul>` : ""}</div>`}
    <${ErrorNote} error=${error} />
  </div>`;
}
