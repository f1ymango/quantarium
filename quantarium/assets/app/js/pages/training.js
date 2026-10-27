// Static, the training ground: cells and their replay and scenario results.
import { html, useState, useMemo, useAsync, Icon, Loading, ErrorNote, navigate } from "../ui.js";
import { get, command } from "../api.js";
import { CellCard, statRanges } from "./home.js";
import { PickAndDeploy } from "./pick.js";
import { HelpButton } from "../tutorial/help.js";

export function TrainingPage() {
  const cards = useAsync(() => get("cells", { scope: "static" }), []);
  const [testing, setTesting] = useState(false);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState(null);
  const all = cards.data || [];
  const trained = all.filter((card) => card.result);
  const untested = all.filter((card) => !card.result && card.status !== "draft");
  const ranges = useMemo(() => statRanges(trained), [all]);
  const addCell = async () => {
    setAdding(true);
    setError(null);
    try {
      const record = await command("cell-create");
      navigate(`cells/${record.id}?tab=configure`);
    } catch (problem) {
      setError(problem);
      setAdding(false);
    }
  };
  return html`<div class="content">
    <div class="row between">
      <div><div class="row"><h1>Static</h1><${HelpButton} topic="replay" /></div><p class="muted" style="margin:2px 0 0">The training ground: replay history and scenarios as fast as your laptop allows.</p></div>
      <div class="row">
        <button data-tour="static-test" onClick=${() => setTesting(true)}><${Icon} name="play" size=${14} /> Test a cell</button>
        <button class="primary" onClick=${addCell} disabled=${adding}><${Icon} name="plus" size=${16} /> Add cell</button>
      </div>
    </div>
    <${ErrorNote} error=${error || cards.error} />
    ${cards.loading && !cards.data ? html`<${Loading} />` : html`
      ${trained.length ? html`<section class="stack">
          <div data-tour="static-results" class="card-grid">${trained.map((card) => html`<${CellCard} key=${card.id} card=${card} ranges=${ranges} />`)}</div></section>`
        : html`<div class="panel empty"><div class="empty-art"><${Icon} name="static" size=${36} /></div>
            <h2 style="margin:0">Nothing has trained yet</h2>
            <p class="muted" style="margin:0;max-width:440px">Replay a cell over historical prices, or test it across scenarios such as a crash or a recession.</p>
            ${all.length ? html`<button class="primary" onClick=${() => setTesting(true)}>Test a cell</button>`
              : html`<button class="primary" onClick=${addCell} disabled=${adding}><${Icon} name="plus" size=${16} /> Add cell</button>`}</div>`}
      ${untested.length > 0 && html`<section class="stack"><h2 style="margin:0">Ready to train</h2>
        <div class="card-grid">${untested.map((card) => html`<${CellCard} key=${card.id} card=${card} ranges=${ranges} />`)}</div></section>`}
      <p class="hint">Every run is listed in the <a href="#/log?view=static">Log</a>.</p>`}
    ${testing && html`<${PickAndDeploy} title="Test a cell" modes=${["scenarios", "replay"]}
      onClose=${() => setTesting(false)} onDone=${() => { setTesting(false); cards.reload(); }} />`}
  </div>`;
}
