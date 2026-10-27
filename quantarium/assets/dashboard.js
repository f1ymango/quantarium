"use strict";

const data = JSON.parse(document.getElementById("quantarium-data").textContent);
const timeKey = data.kind === "live" ? "timestamp" : "date";
const palette = ["#25876e", "#ce7d45", "#537bb3", "#a96392", "#6e9d54", "#cc5f68", "#8a73c0", "#559da3"];
const svgNS = "http://www.w3.org/2000/svg";
const cellNames = data.cells.map(cell => cell.name);
const selected = new Set(cellNames.slice(0, 8));
let sortKey = "total_return_pct";
let sortDescending = true;

function text(id, value) { document.getElementById(id).textContent = value; }
function number(value, digits = 0) {
  return value == null ? "—" : Number(value).toLocaleString(undefined, {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  });
}
function percent(value, signed = false) {
  if (value == null) return "—";
  return `${signed && value > 0 ? "+" : ""}${number(value, 2)}%`;
}
function compactTime(value) {
  if (!value) return "—";
  return data.kind === "live" ? value.replace("T", " ").slice(0, 16) + " UTC" : value;
}
function svgNode(tag, attributes, content) {
  const node = document.createElementNS(svgNS, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, String(value));
  if (content != null) node.textContent = content;
  return node;
}

function renderOverview() {
  text("experiment-title", data.experiment_id);
  const modeName = {static: "STATIC", feed: "FEED BATCH", live: "LIVE FEED"}[data.kind];
  text("mode-label", `${modeName} EXPERIMENT`);
  const status = document.getElementById("experiment-status");
  status.textContent = data.status.replaceAll("_", " ");
  status.className = `status-pill ${data.status}`;
  text("snapshot-time", `Snapshot generated ${compactTime(data.generated_utc)}`);
  text("benchmark-label", data.benchmark ? `Benchmark cell: ${data.benchmark}` : "No benchmark selected");
  const failed = data.cells.filter(cell => cell.status === "failed").length;
  text("kpi-cells", number(data.cells.length));
  text("kpi-cell-detail", failed ? `${failed} cell${failed === 1 ? "" : "s"} failed` : "Independent accounts");
  const ranked = data.cells.filter(cell => cell.total_return_pct != null)
    .sort((a, b) => b.total_return_pct - a.total_return_pct);
  text("kpi-return", ranked.length ? percent(ranked[0].total_return_pct, true) : "—");
  text("kpi-return-detail", ranked.length ? ranked[0].name : "Awaiting valuations");
  const drawdowns = data.cells.filter(cell => cell.max_drawdown_pct != null)
    .sort((a, b) => b.max_drawdown_pct - a.max_drawdown_pct);
  text("kpi-drawdown", drawdowns.length ? percent(drawdowns[0].max_drawdown_pct) : "—");
  text("kpi-drawdown-detail", drawdowns.length ? drawdowns[0].name : "Awaiting valuations");
  text("kpi-orders", number(data.cells.reduce((total, cell) => total + cell.orders_filled, 0)));
  const chartRows = data.curve.length;
  text("sample-note", chartRows < data.full_curve_rows
    ? `Chart embeds ${number(chartRows)} high and low points from ${number(data.full_curve_rows)} saved valuations. Table metrics use the full history.`
    : `Chart embeds all ${number(chartRows)} saved valuations.`);
}

function renderLegend() {
  const legend = document.getElementById("legend");
  legend.replaceChildren();
  data.cells.forEach((cell, index) => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = selected.has(cell.name);
    input.setAttribute("aria-label", `Show ${cell.name} on chart`);
    input.addEventListener("change", () => {
      if (input.checked) selected.add(cell.name); else selected.delete(cell.name);
      renderChart();
      updateToggleAll();
    });
    const swatch = document.createElement("span");
    swatch.className = "swatch";
    swatch.style.backgroundColor = palette[index % palette.length];
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = cell.name;
    label.append(input, swatch, name);
    legend.append(label);
  });
  updateToggleAll();
}

function updateToggleAll() {
  const button = document.getElementById("toggle-all");
  button.hidden = cellNames.length <= 8;
  button.textContent = selected.size === cellNames.length ? "Show first 8" : "Show all cells";
}

function renderChart() {
  const svg = document.getElementById("equity-chart");
  svg.replaceChildren();
  const points = data.curve.filter(point => selected.has(point.cell));
  const times = [...new Set(points.map(point => point[timeKey]))].sort();
  const empty = times.length === 0;
  document.getElementById("chart-empty").hidden = !empty;
  svg.hidden = empty;
  text("chart-range", empty ? "No range" : `${compactTime(times[0])} → ${compactTime(times[times.length - 1])}`);
  if (empty) return;
  const left = 58, right = 980, top = 17, bottom = 318;
  const firstTime = Date.parse(times[0]);
  const lastTime = Date.parse(times[times.length - 1]);
  const byCell = new Map();
  let low = 100, high = 100;
  for (const point of points) {
    low = Math.min(low, point.index_100);
    high = Math.max(high, point.index_100);
    if (!byCell.has(point.cell)) byCell.set(point.cell, []);
    byCell.get(point.cell).push(point);
  }
  const spread = high - low || 4;
  low -= spread * 0.1;
  high += spread * 0.1;
  const x = time => left + (Date.parse(time) - firstTime) * (right - left)
    / Math.max(1, lastTime - firstTime);
  const y = value => bottom - (value - low) * (bottom - top) / (high - low);
  for (let tick = 0; tick <= 4; tick++) {
    const value = low + (high - low) * tick / 4;
    const coordinate = y(value);
    svg.append(svgNode("line", {x1: left, y1: coordinate, x2: right, y2: coordinate, class: "grid-line"}));
    svg.append(svgNode("text", {x: 47, y: coordinate + 4, "text-anchor": "end", class: "axis-label"}, number(value, 1)));
  }
  const labels = [...new Set([0, Math.floor((times.length - 1) / 2), times.length - 1])];
  for (const index of labels) {
    svg.append(svgNode("text", {x: x(times[index]), y: 349,
      "text-anchor": index === 0 ? "start" : index === times.length - 1 ? "end" : "middle",
      class: "axis-label"}, compactTime(times[index])));
  }
  data.cells.forEach((cell, colorIndex) => {
    if (!selected.has(cell.name)) return;
    const series = (byCell.get(cell.name) || [])
      .sort((a, b) => a[timeKey].localeCompare(b[timeKey]));
    if (!series.length) return;
    const commands = series.map((point, index) =>
      `${index ? "L" : "M"}${x(point[timeKey]).toFixed(2)},${y(point.index_100).toFixed(2)}`);
    if (series.length === 1) commands.push(`l0.1,0`);
    const path = svgNode("path", {d: commands.join(" "), class: "path",
      stroke: palette[colorIndex % palette.length]});
    path.append(svgNode("title", {}, `${cell.name}: ${series.length} plotted valuations`));
    svg.append(path);
  });
}

function sortValue(cell, key) {
  if (key === "bars") return cell.minutes ?? cell.sessions;
  if (key === "status") return cell.status || data.status;
  return cell[key];
}

function renderTable() {
  const query = document.getElementById("cell-search").value.trim().toLowerCase();
  const rows = data.cells.filter(cell => `${cell.name} ${cell.mode}`.toLowerCase().includes(query));
  rows.sort((a, b) => {
    const left = sortValue(a, sortKey), right = sortValue(b, sortKey);
    if (left == null) return right == null ? 0 : 1;
    if (right == null) return -1;
    const result = typeof left === "number" && typeof right === "number"
      ? left - right : String(left).localeCompare(String(right));
    return sortDescending ? -result : result;
  });
  const body = document.getElementById("cell-rows");
  const fragment = document.createDocumentFragment();
  for (const cell of rows) {
    const row = document.createElement("tr");
    const state = cell.status === "failed" ? "failed"
      : data.status === "complete" || data.status === "completed" ? "completed"
      : data.status === "halted" ? "stopped"
      : cell.status || data.status;
    const fields = [cell.name, cell.mode, state, percent(cell.total_return_pct, true),
      percent(cell.max_drawdown_pct), number(cell.minutes ?? cell.sessions),
      number(cell.orders_filled), number(cell.traded_notional, 2)];
    fields.forEach((value, index) => {
      const column = document.createElement("td");
      if (index >= 3) column.className = "numeric";
      if (index === 2) {
        const badge = document.createElement("span");
        badge.className = `cell-state ${state}`;
        badge.textContent = state;
        if (cell.error) badge.title = cell.error;
        column.append(badge);
      } else {
        column.textContent = value;
        if (index === 3 && cell.total_return_pct != null)
          column.classList.add(cell.total_return_pct >= 0 ? "gain" : "loss");
      }
      row.append(column);
    });
    fragment.append(row);
  }
  body.replaceChildren(fragment);
  document.getElementById("table-empty").hidden = rows.length !== 0;
}

document.getElementById("cell-search").addEventListener("input", renderTable);
document.querySelectorAll("[data-sort]").forEach(button => button.addEventListener("click", () => {
  const key = button.dataset.sort;
  sortDescending = key === sortKey ? !sortDescending : key !== "name" && key !== "mode";
  sortKey = key;
  renderTable();
}));
document.getElementById("toggle-all").addEventListener("click", () => {
  selected.clear();
  (cellNames.length === document.querySelectorAll("#legend input:checked").length
    ? cellNames.slice(0, 8) : cellNames).forEach(name => selected.add(name));
  renderLegend();
  renderChart();
});

renderOverview();
renderLegend();
renderChart();
renderTable();
