import { html, useState, useAsync, Loading, ErrorNote } from "../ui.js";
import { get, command } from "../api.js";
import { HelpButton } from "../tutorial/help.js";

const STATUS_BADGE = { pending: "warn", approved: "up", rejected: "neutral", stale: "neutral", failed: "down" };

function summary(item) {
  const params = item.params;
  const cells = Object.values(item.pins.cells).map((cell) => cell.name);
  const named = cells.length ? cells.join(", ") : "";
  switch (item.action) {
    case "deploy-feed": return `Start Feed forward test ${params.feed_id} with ${named}`;
    case "feed-batch": return `Advance the Feed experiment in ${params.config}`;
    case "retire-cell": return `Retire ${params.cell} from ${params.id}`;
    case "cell-archive": return `Archive ${named}`;
    case "cell-restore": return `Restore ${named}`;
    case "holdout-test": return `Test ${named} on your holdout scenarios`;
    case "lab-register": return `Pre-register ${params.name} in the Lab with ${named}`;
    default: return item.action;
  }
}

function Script({ script, onRead }) {
  const [source, setSource] = useState(null);
  const [error, setError] = useState(null);
  const open = async () => {
    setError(null);
    try {
      const read = await get(`scripts/${script.key}`);
      setSource(read.sha256 === script.sha256 ? read.source : false);
      onRead();
    } catch (problem) { setError(problem); }
  };
  return html`<li class="inbox-script">
    <div class="row between"><span class="mono">${script.key}</span>
      <span>${script.agent_unreviewed ? html`<span class="badge warn">written by an agent, not reviewed</span>` : html`<span class="badge neutral">your code or reviewed</span>`}
        <button class="ghost" onClick=${open}>${source === null ? "Read" : "Reload"}</button></span></div>
    <${ErrorNote} error=${error} />
    ${source === false && html`<p class="hint">This script changed after the proposal was made, so approving will be refused.</p>`}
    ${source && html`<pre class="agent-source">${source}</pre>`}
  </li>`;
}

function Proposal({ item, onDecided }) {
  const pending = item.status === "pending";
  const mustRead = item.action === "deploy-feed" ? item.scripts.filter((script) => script.agent_unreviewed) : [];
  const [read, setRead] = useState(new Set());
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const allRead = mustRead.every((script) => read.has(script.key));
  const decide = async (decision) => {
    if (decision === "approve" && !confirm(`Approve: ${summary(item)}?\n\nQuantarium will do this as you.`)) return;
    setBusy(true); setError(null);
    try { await command("proposal-decide", { id: item.id, decision, note: note || undefined }); }
    catch (problem) { setError(problem); }
    window.dispatchEvent(new Event("quantarium-proposals-changed"));
    onDecided();
    setBusy(false);
  };
  return html`<div class="panel inbox-item">
    <div class="row between"><h2 style="margin:0">${summary(item)}</h2>
      <span class=${`badge ${STATUS_BADGE[item.status] || "neutral"}`}>${item.status}</span></div>
    <p class="muted" style="margin:4px 0 12px">Proposed by ${item.connection_name} on ${new Date(item.created_utc).toLocaleString()}</p>
    ${item.variants_tested > 0 && html`<p class=${item.variants_tested >= 20 ? "notice" : "hint"}>The agent had simulated ${item.variants_tested} cell variant${item.variants_tested === 1 ? "" : "s"} before proposing this.${item.variants_tested >= 20 ? " The best of many tries usually does worse going forward." : ""}</p>`}
    ${item.action === "holdout-test" && html`<p class="hint">Runs a suite on your holdout scenarios. Only you will see the results, in Log. Each cell version gets one holdout test.</p>`}
    <h3>The agent's reasoning</h3>
    <p class="hint" style="margin:0 0 4px">Written by the agent. Check it against the results yourself.</p>
    <p class="inbox-rationale">${item.rationale}</p>
    ${item.evidence.length > 0 && html`<h3>Evidence it cites</h3><ul>${item.evidence.map((entry) => html`<li class="mono">${entry}</li>`)}</ul>`}
    <h3>What will happen</h3>
    <pre class="agent-source">${JSON.stringify({ action: item.action, ...item.params }, null, 2)}</pre>
    ${Object.keys(item.pins.cells).length > 0 && html`<h3>Cells</h3><ul>${Object.entries(item.pins.cells).map(([id, cell]) =>
      html`<li><a href=${`#/cells/${id}`}>${cell.name}</a> <span class="muted">version ${cell.version}</span></li>`)}</ul>`}
    ${item.scripts.length > 0 && html`<h3>Scripts</h3>
      ${mustRead.length > 0 && pending && html`<p class="hint">Approving this forward test also reviews the agent's scripts below. Read each one first.</p>`}
      <ul class="inbox-scripts">${item.scripts.map((script) => html`<${Script} script=${script}
        onRead=${() => setRead(new Set([...read, script.key]))} />`)}</ul>`}
    ${item.note && html`<p><strong>Your note:</strong> ${item.note}</p>`}
    ${item.error && html`<p class="hint">Failed: ${item.error}</p>`}
    ${item.job_id && html`<p class="hint">Started job <a href="#/activity" class="mono">${item.job_id}</a></p>`}
    <${ErrorNote} error=${error} />
    ${pending && html`<div class="inbox-actions">
      <input value=${note} onInput=${(event) => setNote(event.target.value)} placeholder="Note for the agent (optional)" maxlength="500" />
      <div class="row" style="gap:8px;justify-content:flex-end">
        <button class="ghost" disabled=${busy} onClick=${() => decide("reject")}>Reject</button>
        <button class="primary" disabled=${busy || !allRead} title=${allRead ? "" : "Read each agent-written script first"}
          onClick=${() => decide("approve")}>Approve</button></div></div>`}
  </div>`;
}

export function InboxPage() {
  const [tab, setTab] = useState("pending");
  const proposals = useAsync(() => get("agents/proposals"), []);
  if (proposals.loading && !proposals.data) return html`<div class="content"><${Loading} /></div>`;
  if (proposals.error) return html`<div class="content"><${ErrorNote} error=${proposals.error} /></div>`;
  const items = proposals.data.filter((item) => (tab === "pending") === (item.status === "pending"));
  return html`<div class="content inbox-page">
    <div><div class="row"><h1>Inbox</h1><${HelpButton} topic="inbox" /></div>
      <p class="muted" style="margin:4px 0 0">Things agents asked to do that need you: Lab registrations, Feed forward tests, Advances, retiring, archiving. Nothing here happens until you approve it.</p></div>
    <div class="row" style="gap:8px">
      <button class=${tab === "pending" ? "primary" : "ghost"} onClick=${() => setTab("pending")}>Waiting</button>
      <button class=${tab === "decided" ? "primary" : "ghost"} onClick=${() => setTab("decided")}>Decided</button>
      <span class="spacer"></span><button class="ghost" onClick=${proposals.reload}>Refresh</button></div>
    ${items.length ? items.map((item) => html`<${Proposal} key=${item.id} item=${item} onDecided=${proposals.reload} />`)
      : html`<div class="panel"><p class="muted" style="margin:0">${tab === "pending" ? "Nothing is waiting for you." : "No decided proposals yet."}</p></div>`}
  </div>`;
}
