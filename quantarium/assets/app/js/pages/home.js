import { html, useState, useMemo, useAsync, Spark, ModeBadge, Change, fmt, Icon, Loading, ErrorNote, EvidenceBadge, navigate } from "../ui.js";
import { get, command } from "../api.js";
import { HelpButton } from "../tutorial/help.js";

const FILTERS = [
  ["all", "All"], ["simple", "Simple"], ["complex", "Complex"], ["draft", "Drafts"], ["deployed", "Deployed"],
];
const STATS = [
  ["Return", "total_return_pct", (value) => html`<${Change} value=${value} />`],
  ["Drawdown", "max_drawdown_pct", (value) => html`<span class="num">${fmt.num(value, 1)}%</span>`],
  ["Sharpe", "sharpe_ratio_rf0", (value) => html`<span class="num">${fmt.num(value, 2)}</span>`],
  ["Sessions", "sessions", (value) => html`<span class="num">${fmt.int(value)}</span>`],
];

export function StatSheet({ result, ranges }) {
  if (!result || result.error) {
    return html`<div class="stat-sheet faint">${result?.error || "No results yet"}</div>`;
  }
  return html`<div class="stat-sheet">${STATS.map(([label, key, show]) => {
    const value = result[key];
    const [low, high] = ranges[key] || [0, 1];
    const width = value === null || value === undefined || high === low ? 0 : 8 + 92 * (value - low) / (high - low);
    const color = key === "total_return_pct" ? (value >= 0 ? "up" : "down") : key === "max_drawdown_pct" ? "down" : "";
    return html`<div class="stat-line"><span class="label">${label}</span>
      <span class="stat-bar" aria-hidden="true"><i class=${color} style=${`width:${width}%`}></i></span>${show(value)}</div>`;
  })}</div>`;
}

export function statRanges(cards) {
  return Object.fromEntries(STATS.map(([, key]) => {
    const values = cards.map((card) => card.result?.[key]).filter((value) => typeof value === "number");
    return [key, values.length ? [Math.min(0, ...values), Math.max(0, ...values)] : [0, 1]];
  }));
}

export function CellCard({ card, ranges, managing = false, selected = false, onToggle }) {
  const open = () => (managing ? onToggle(card.id) : navigate(`cells/${card.id}`));
  const draft = card.status === "draft";
  const unnamed = card.name.startsWith("Unnamed cell");
  const deployments = Object.entries(card.deployments).filter(([, count]) => count);
  return html`<article data-tour=${`card-${card.name}`} class=${`card ${card.mode} ${draft ? "draft" : ""} ${selected ? "selected" : ""}`}
      tabindex="0" onClick=${open} onKeyDown=${(event) => (event.key === "Enter" || event.key === " ") && (event.preventDefault(), open())}
      aria-label=${`${card.name}, ${card.mode} cell, ${card.status}`}>
    ${managing && html`<input class="card-check" type="checkbox" checked=${selected} tabindex="-1" aria-label=${`Select ${card.name}`}
      onClick=${(event) => { event.stopPropagation(); onToggle(card.id); }} />`}
    <div class="card-head">
      <span class=${`status-dot status-${card.status}`} title=${card.status}></span>
      <span class=${`card-name ${unnamed ? "unnamed" : ""}`}>${card.name}</span>
      ${!managing && html`<${ModeBadge} mode=${card.mode} />`}
    </div>
    ${card.result?.spark?.length > 1
      ? html`<div class="card-portrait" title=${card.result.spark_label ? `Equity on ${card.result.spark_label}` : "Equity"}><${Spark} values=${card.result.spark} height=${64} /></div>`
      : html`<div class="card-portrait blank">${draft ? "Not configured" : card.result ? html`<${EvidenceBadge} evidence=${card.result.evidence} />` : "Ready to run"}</div>`}
    <${StatSheet} result=${card.result} ranges=${ranges} />
    <div class="card-foot">
      <span>${card.holdings} holding${card.holdings === 1 ? "" : "s"}</span><span>·</span>
      <span>${card.sleeves} script${card.sleeves === 1 ? "" : "s"}</span>
      ${deployments.map(([kind, count]) => html`<span class="badge neutral">${kind[0].toUpperCase() + kind.slice(1)} ×${count}</span>`)}
      ${(card.model_card || card.asks || card.allocator) && html`<span class="badge info"
        title=${[card.model_card && "Uses a risk card", card.asks && `Asks ${card.asks}`, card.allocator && `Allocator: ${card.allocator}`]
          .filter(Boolean).join(" · ") + ". Its evidence is on the Lab page's Supercells view."}>Supercell</span>`}
      ${(card.model_card || card.asks || card.allocator) && html`<span class="badge neutral" title=${card.lab
        ? `${card.lab.scenarios} scenario(s), observed attempt ${card.lab.attempt}` : "No registered Lab test for this version"}>
        Lab: ${card.lab?.status?.replaceAll("_", " ") || "not tested"}</span>`}
      ${card.version > 1 && html`<span class="badge neutral">v${card.version}</span>`}
    </div>
  </article>`;
}

export function HomePage() {
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("recent");
  const [archived, setArchived] = useState(false);
  const [managing, setManaging] = useState(false);
  const [selected, setSelected] = useState(new Set());
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const cells = useAsync(() => get("cells", { archived: archived ? "1" : "" }), [archived]);

  const cards = cells.data || [];
  const ranges = useMemo(() => statRanges(cards), [cards]);

  const shown = cards
    .filter((card) => filter === "all" || card.mode === filter
      || (filter === "draft" && card.status === "draft")
      || (filter === "deployed" && Object.values(card.deployments).some(Boolean)))
    .filter((card) => card.name.toLowerCase().includes(search.trim().toLowerCase()))
    .sort((a, b) => sort === "name" ? a.name.localeCompare(b.name)
      : sort === "return" ? (b.result?.total_return_pct ?? -Infinity) - (a.result?.total_return_pct ?? -Infinity)
      : b.updated_utc.localeCompare(a.updated_utc));

  const addCell = async () => {
    setBusy("add");
    try {
      const record = await command("cell-create");
      navigate(`cells/${record.id}?tab=configure`);
    } catch (problem) { setError(problem); setBusy(null); }
  };
  const toggle = (id) => setSelected((previous) => {
    const next = new Set(previous);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });
  const bulk = async (name) => {
    setBusy(name); setError(null);
    try {
      await command(name, { ids: [...selected] });
      setSelected(new Set());
      cells.reload();
    } catch (problem) { setError(problem); }
    setBusy(null);
  };
  const stopManaging = () => { setManaging(false); setSelected(new Set()); };

  return html`<div class="content">
    <div class="row between">
      <div><div class="row" data-tour="home-title"><h1>${archived ? "Archived cells" : "Home"}</h1><${HelpButton} topic="home-cards" /></div>
        <p class="muted" style="margin:2px 0 0">${archived ? "Restore a cell to bring it back." : "All your cells. Each one is an algorithm with its own account."}</p></div>
      <div class="row">
        <button onClick=${() => (managing ? stopManaging() : setManaging(true))}>${managing ? "Done" : "Manage"}</button>
        <button data-tour="home-add" class="primary" onClick=${addCell} disabled=${busy === "add"}><${Icon} name="plus" size=${16} /> Add cell</button>
      </div>
    </div>
    <${ErrorNote} error=${error || cells.error} />
    ${cards.length > 0 && html`<div class="row between">
      <div class="row">${FILTERS.map(([key, label]) => html`<button class=${`chip ${filter === key ? "on" : ""}`}
        aria-pressed=${filter === key} onClick=${() => setFilter(key)}>${label}</button>`)}</div>
      <div class="row">
        <input type="search" placeholder="Search cells" aria-label="Search cells" value=${search} onInput=${(event) => setSearch(event.target.value)} />
        <select aria-label="Sort" value=${sort} onChange=${(event) => setSort(event.target.value)}>
          <option value="recent">Recently edited</option><option value="name">Name</option><option value="return">Best return</option>
        </select>
      </div></div>`}
    ${cells.loading && !cells.data ? html`<${Loading} />` : cards.length === 0
      ? html`<div class="panel empty">
          <div class="empty-art"><${Icon} name="cells" size=${36} /></div>
          <h2 style="margin:0">${archived ? "No archived cells" : "Start your first cell"}</h2>
          <p class="muted" style="margin:0;max-width:420px">${archived
            ? "Cells you archive appear here, and you can restore them."
            : "A cell holds stocks, runs your scripts, and keeps its own simulated account and record."}</p>
          ${archived ? html`<button onClick=${() => setArchived(false)}>Back to your cells</button>`
            : html`<button class="primary" onClick=${addCell}><${Icon} name="plus" size=${16} /> Add cell</button>`}
        </div>`
      : html`<div class="card-grid">${shown.map((card) => html`<${CellCard} key=${card.id} card=${card} ranges=${ranges}
          managing=${managing} selected=${selected.has(card.id)} onToggle=${toggle} />`)}</div>
        ${!shown.length && html`<p class="muted">No cells match.</p>`}`}
    ${managing && html`<div class="manage-bar" role="toolbar" aria-label="Manage cells">
      <span class="muted">${selected.size} selected</span>
      ${archived
        ? html`<button onClick=${() => bulk("cell-restore")} disabled=${!selected.size || busy}>Restore</button>`
        : html`<button onClick=${() => bulk("cell-duplicate")} disabled=${!selected.size || busy}>Duplicate</button>
               <button onClick=${() => bulk("cell-archive")} disabled=${!selected.size || busy}>Archive</button>`}
      <button class="danger" onClick=${() => bulk("cell-delete")} disabled=${!selected.size || busy}
        title="Only cells without results can be deleted"><${Icon} name="trash" size=${15} /> Delete</button>
      <label class="row" style="gap:6px;font-size:13px;padding:0 6px"><input type="checkbox" checked=${archived}
        onChange=${(event) => { setArchived(event.target.checked); setSelected(new Set()); }} /> Show archived</label>
      <button class="ghost" onClick=${stopManaging}>Done</button>
    </div>`}
  </div>`;
}
