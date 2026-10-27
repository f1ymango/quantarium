// The cell page's center view, chosen by how many holdings there are:
// up to 4 are charted, up to 30 get tiles, and more get an aggregate view.
import { html, useMemo, useState, TimeChart, Change, Spark, Metric, fmt, seriesColor } from "../ui.js";

export function ChartView({ holdings }) {
  const { series, fills } = holdings;
  const symbols = Object.keys(series.prices || {});
  const priceSeries = useMemo(() => symbols.map((symbol, index) => ({
    label: symbol, values: series.prices[symbol], color: seriesColor(index),
  })), [holdings]);
  // Markers only make sense against a single price axis: with one symbol,
  // draw fills on its line; with several, list them below instead.
  const markers = useMemo(() => (symbols.length === 1
    ? fills.map((fill) => ({ time: fill.time, value: fill.price, side: fill.side })) : []), [holdings]);
  const equity = useMemo(() => [{ label: "Equity", values: series.equity, color: "var(--text-2)" }], [holdings]);
  const cash = useMemo(() => [
    { label: "Equity", values: series.equity, color: seriesColor(0) },
    { label: "Cash", values: series.cash, color: seriesColor(2), dash: [4, 4] },
  ], [holdings]);
  return html`<div class="stack">
    <div><h3>${symbols.length === 1 ? `${symbols[0]} price` : "Prices"}</h3>
      <${TimeChart} times=${series.times} series=${priceSeries} height=${280} markers=${markers} />
      ${symbols.length === 1 && fills.length > 0 && html`<div class="legend"><span><i style="background:var(--up)"></i>Buy</span><span><i style="background:var(--down)"></i>Sell</span></div>`}</div>
    <div><h3>Account</h3><${TimeChart} times=${series.times} series=${cash} height=${170} /></div>
    ${symbols.length > 1 && fills.length > 0 && html`<${FillTable} fills=${fills} />`}
  </div>`;
}

function FillTable({ fills }) {
  const recent = fills.slice(-12).reverse();
  return html`<div><h3>Recent fills</h3><div class="table-wrap"><table class="data">
    <thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th class="num">Shares</th><th class="num">Price</th></tr></thead>
    <tbody>${recent.map((fill) => html`<tr><td>${fmt.date(fill.time)}</td><td class="mono">${fill.symbol}</td>
      <td><span class=${`badge ${fill.side === "buy" ? "up" : "down"}`}>${fill.side}</span></td>
      <td class="num">${fmt.int(fill.shares)}</td><td class="num">${fmt.num(fill.price)}</td></tr>`)}</tbody></table></div></div>`;
}

export function TileView({ holdings }) {
  const [sort, setSort] = useState("weight");
  const rows = [...holdings.summary].sort((a, b) => sort === "name" ? a.symbol.localeCompare(b.symbol)
    : sort === "return" ? (b.return_pct ?? -Infinity) - (a.return_pct ?? -Infinity)
    : (b.weight_pct ?? 0) - (a.weight_pct ?? 0));
  return html`<div class="stack">
    <div class="row between"><h3 style="margin:0">${rows.length} holdings</h3>
      <select aria-label="Sort holdings" value=${sort} onChange=${(event) => setSort(event.target.value)}>
        <option value="weight">By weight</option><option value="return">By return</option><option value="name">By name</option></select></div>
    <div class="tiles">${rows.map((row) => html`<div class="tile">
      <span class="sym mono">${row.symbol}</span>
      <${Change} value=${row.return_pct} className="chg" />
      <${Spark} values=${holdings.series.sparks?.[row.symbol]?.filter((value) => value !== null)} height=${30} />
      <span class="meta"><span>${fmt.num(row.weight_pct, 1)}% weight</span><span>${fmt.money(row.value)}</span></span>
    </div>`)}</div>
    <p class="hint">Change is each stock's price move over the shown range; weight and value are as of its end.</p>
  </div>`;
}

export function AggregateView({ holdings }) {
  const [query, setQuery] = useState("");
  const rows = holdings.summary;
  const withReturn = rows.filter((row) => row.return_pct !== null);
  const up = withReturn.filter((row) => row.return_pct > 0).length;
  const byWeight = [...rows].sort((a, b) => (b.weight_pct ?? 0) - (a.weight_pct ?? 0));
  const topTen = byWeight.slice(0, 10).reduce((sum, row) => sum + (row.weight_pct || 0), 0);
  const byReturn = [...withReturn].sort((a, b) => b.return_pct - a.return_pct);
  const equity = holdings.series.equity, cash = holdings.series.cash;
  const cashShare = equity.map((value, index) => (value ? 100 * cash[index] / value : 0));
  const bins = useMemo(() => {
    if (!withReturn.length) return [];
    const values = withReturn.map((row) => row.return_pct);
    const low = Math.min(...values), high = Math.max(...values), count = 16;
    const width = (high - low) / count || 1;
    const result = Array.from({ length: count }, (_, index) => ({ from: low + index * width, n: 0 }));
    values.forEach((value) => { result[Math.min(count - 1, Math.floor((value - low) / width))].n += 1; });
    return result;
  }, [holdings]);
  const tallest = Math.max(1, ...bins.map((bin) => bin.n));
  const filtered = byWeight.filter((row) => row.symbol.includes(query.trim().toUpperCase()));
  const movers = (list, title) => html`<div><h3>${title}</h3><table class="data"><tbody>${list.map((row) =>
    html`<tr><td class="mono">${row.symbol}</td><td class="num"><${Change} value=${row.return_pct} /></td><td class="num faint">${fmt.num(row.weight_pct, 2)}%</td></tr>`)}</tbody></table></div>`;
  return html`<div class="stack">
    <div class="summary-strip">
      <${Metric} label="Holdings">${fmt.int(rows.length)}<//>
      <${Metric} label="Up over range">${withReturn.length ? `${Math.round(100 * up / withReturn.length)}%` : "—"}<//>
      <${Metric} label="Largest weight">${fmt.num(byWeight[0]?.weight_pct, 1)}%<//>
      <${Metric} label="Top-10 share">${fmt.num(topTen, 1)}%<//>
      <${Metric} label="Cash now">${fmt.num(cashShare[cashShare.length - 1], 1)}%<//>
    </div>
    <div class="grid-2">
      <div><h3>Holding returns</h3>
        <div class="histogram" role="img" aria-label="Histogram of holding returns">${bins.map((bin) => html`<div
          title=${`${fmt.pct(bin.from)} and up: ${bin.n}`} style=${`height:${100 * bin.n / tallest}%;background:${bin.from >= 0 ? "var(--up)" : "var(--down)"}`}></div>`)}</div>
        <div class="row between faint" style="font-size:12px"><span>${fmt.pct(bins[0]?.from)}</span><span>${fmt.pct(byReturn[0]?.return_pct)}</span></div></div>
      <div><h3>Cash share over time</h3><${TimeChart} times=${holdings.series.times}
        series=${[{ label: "Cash %", values: cashShare, color: "var(--text-2)" }]} height=${150} /></div>
    </div>
    <div class="grid-2">${movers(byReturn.slice(0, 5), "Top movers")}${movers(byReturn.slice(-5).reverse(), "Bottom movers")}</div>
    <div><div class="row between"><h3 style="margin:0">All holdings</h3>
      <input type="search" placeholder="Find a symbol" aria-label="Find a symbol" value=${query} onInput=${(event) => setQuery(event.target.value)} /></div>
      <div class="table-wrap" style="max-height:360px;overflow:auto"><table class="data">
        <thead><tr><th>Symbol</th><th class="num">Weight</th><th class="num">Value</th><th class="num">Change</th><th class="num">Shares</th></tr></thead>
        <tbody>${filtered.slice(0, 200).map((row) => html`<tr><td class="mono">${row.symbol}</td><td class="num">${fmt.num(row.weight_pct, 2)}%</td>
          <td class="num">${fmt.money(row.value)}</td><td class="num"><${Change} value=${row.return_pct} /></td><td class="num">${fmt.int(row.shares)}</td></tr>`)}</tbody>
      </table></div>${filtered.length > 200 && html`<p class="hint">Showing 200 of ${filtered.length}; search to narrow.</p>`}</div>
  </div>`;
}

export function SleevePanel({ spec, scripts }) {
  const sleeves = spec.sleeves || [];
  const total = sleeves.reduce((sum, sleeve) => sum + sleeve.allocation, 0);
  if (!sleeves.length) return html`<p class="muted">No scripts yet.</p>`;
  const label = (sleeve) => (sleeve.decisions !== undefined ? `Decision file ${sleeve.decisions}`
    : sleeve.inbox !== undefined ? `Forward inbox ${sleeve.inbox}`
    : sleeve.decider !== undefined ? `Decider ${sleeve.decider}` : `${sleeve.class}`);
  if (sleeves.length <= 4) {
    return html`<div class="stack">
      <div class="alloc-bar" role="img" aria-label="Allocation by sleeve">${sleeves.map((sleeve, index) =>
        html`<span style=${`width:${100 * sleeve.allocation}%;background:${seriesColor(index)}`} title=${label(sleeve)}></span>`)}</div>
      <div class="stack" style="gap:6px">${sleeves.map((sleeve, index) => html`<div class="row between" style="font-size:13px">
        <span><i style=${`display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;background:${seriesColor(index)}`}></i>${label(sleeve)}</span>
        <span class="num">${fmt.num(100 * sleeve.allocation, 0)}%</span></div>`)}
        ${total < 0.999 && html`<div class="row between faint" style="font-size:13px"><span>Kept as cash</span><span class="num">${fmt.num(100 * (1 - total), 0)}%</span></div>`}
      </div>
      <p class="hint">Sleeves share one account, so profit per sleeve isn't measured yet.</p>
    </div>`;
  }
  return html`<table class="data"><thead><tr><th>Script</th><th class="num">Share</th></tr></thead>
    <tbody>${sleeves.map((sleeve) => html`<tr><td>${label(sleeve)}<div class="faint mono">${sleeve.script}</div></td>
      <td class="num">${fmt.num(100 * sleeve.allocation, 0)}%</td></tr>`)}</tbody></table>`;
}
