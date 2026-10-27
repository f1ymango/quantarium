// Choose one of your cells, then open the Run dialog for it with some modes.
import { html, useState, useAsync, Dialog, Loading, ErrorNote, ModeBadge, Change, EvidenceBadge } from "../ui.js";
import { get } from "../api.js";
import { DeployDialog } from "./cell.js";

export function PickAndDeploy({ title, hint, modes, scope, onClose, onDone }) {
  const cards = useAsync(() => get("cells", { scope }), [scope]);
  const [record, setRecord] = useState(null);
  const [error, setError] = useState(null);
  const pick = async (id) => {
    try { setRecord(await get(`cells/${id}`)); } catch (problem) { setError(problem); }
  };
  if (record) return html`<${DeployDialog} record=${record} modes=${modes} onClose=${onClose} onDone=${onDone} />`;
  const ready = (cards.data || []).filter((card) => card.status !== "draft");
  return html`<${Dialog} title=${title} onClose=${onClose}>
    ${hint && html`<p class="muted" style="margin:0">${hint}</p>`}
    ${cards.loading ? html`<${Loading} />` : !ready.length
      ? html`<p class="muted">No configured cells yet. Create one on <a href="#/home" onClick=${onClose}>Home</a>.</p>`
      : html`<div class="stack" style="gap:6px">${ready.map((card) => html`<button data-tour=${`pick-${card.id}`} class="choice" style="grid-template-columns:1fr auto;align-items:center"
          onClick=${() => pick(card.id)}>
          <span class="row" style="gap:8px"><strong>${card.name}</strong><${ModeBadge} mode=${card.mode} />
            <span class="faint">${card.holdings} holding${card.holdings === 1 ? "" : "s"}</span></span>
          <span class="row" style="gap:8px">${card.result
            ? html`<${EvidenceBadge} evidence=${card.result.evidence} /><${Change} value=${card.result.total_return_pct} />`
            : html`<span class="faint">untested</span>`}</span>
        </button>`)}</div>`}
    <${ErrorNote} error=${error || cards.error} />
  <//>`;
}
