import { html, useEffect, useRef, useState, useCallback, useMemo } from "../vendor/htm/standalone.module.js";
import uPlot from "../vendor/uplot/uPlot.esm.js";

export function navigate(path) {
  location.hash = `#/${path}`;
}

// Formatting ----------------------------------------------------------------

export const fmt = {
  pct(value, digits = 1) {
    if (value === null || value === undefined || Number.isNaN(value)) return "—";
    const sign = value > 0 ? "+" : value < 0 ? "−" : "";
    return `${sign}${Math.abs(value).toFixed(digits)}%`;
  },
  num(value, digits = 2) {
    if (value === null || value === undefined || Number.isNaN(value)) return "—";
    return Number(value).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
  },
  money(value) {
    if (value === null || value === undefined) return "—";
    return Number(value).toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
  },
  int(value) {
    return value === null || value === undefined ? "—" : Number(value).toLocaleString();
  },
  date(value) {
    if (!value) return "—";
    return String(value).slice(0, 16).replace("T", " ");
  },
};

export function tone(value) {
  if (value === null || value === undefined || value === 0) return "";
  return value > 0 ? "up" : "down";
}

// A signed change with a glyph, so meaning never depends on color alone.
export function Change({ value, digits = 1, className = "" }) {
  const t = tone(value);
  const glyph = t === "up" ? "▲ " : t === "down" ? "▼ " : "";
  return html`<span class=${`num ${t} ${className}`}>${glyph}${fmt.pct(value, digits)}</span>`;
}

// Icons (stroke paths, 24×24) ---------------------------------------------

const PATHS = {
  cells: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
  static: "M12 8v4l3 2M3.05 11a9 9 0 1 1 .5 4M3 4v5h5",
  lab: "M9 3h6M10 3v7l-5 8a2 2 0 0 0 1.7 3h10.6a2 2 0 0 0 1.7-3l-5-8V3M7 16h10",
  feed: "M4 11a9 9 0 0 1 9 9M4 4a16 16 0 0 1 16 16M5 19h.01",
  scenarios: "M3 17l6-6 4 4 8-8M14 7h7v7",
  activity: "M9 6h11M9 12h11M9 18h11M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2",
  log: "M6 3h9l4 4v14H6zM15 3v4h4M9 11h7M9 15h7M9 19h4",
  workspace: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  agents: "M4 5h16v11H9l-5 4zM9 10h.01M12 10h.01M15 10h.01",
  inbox: "M4 13l2-8h12l2 8M4 13v6h16v-6M4 13h5l1 2h4l1-2h5",
  learn: "M4 5a2 2 0 0 1 2-2h5a2 2 0 0 1 2 2v15a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2zM20 5a2 2 0 0 0-2-2h-3a2 2 0 0 0-2 2v15a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2z",
  collapse: "M4 4h16v16H4zM9 4v16M15 10l-2 2 2 2",
  expand: "M4 4h16v16H4zM9 4v16M13 10l2 2-2 2",
  plus: "M12 5v14M5 12h14",
  menu: "M4 6h16M4 12h16M4 18h16",
  theme: "M12 3a9 9 0 1 0 9 9 7 7 0 0 1-9-9z",
  back: "M15 6l-6 6 6 6",
  trash: "M4 7h16M10 11v6M14 11v6M5 7l1 12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2l1-12M9 7V4h6v3",
  play: "M7 4v16l13-8z",
  close: "M6 6l12 12M18 6L6 18",
};

export function Icon({ name, size = 18 }) {
  return html`<svg viewBox="0 0 24 24" width=${size} height=${size} fill="none" stroke="currentColor"
    stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d=${PATHS[name]} /></svg>`;
}

// Small pieces --------------------------------------------------------------

export function Spark({ values, width = 200, height = 56, color }) {
  if (!values || values.length < 2) return null;
  const min = Math.min(...values), max = Math.max(...values);
  const span = max - min || 1;
  const points = values.map((value, index) =>
    `${(index / (values.length - 1)) * width},${height - 4 - ((value - min) / span) * (height - 8)}`).join(" ");
  const stroke = color || (values[values.length - 1] >= values[0] ? "var(--up)" : "var(--down)");
  return html`<svg viewBox=${`0 0 ${width} ${height}`} preserveAspectRatio="none" width="100%" height=${height} aria-hidden="true">
    <polyline fill="none" stroke=${stroke} stroke-width="1.8" vector-effect="non-scaling-stroke" points=${points} /></svg>`;
}

export function ModeBadge({ mode }) {
  return html`<span class=${`badge ${mode}`}>${mode === "complex" ? "Complex" : "Simple"}</span>`;
}

const EVIDENCE = {
  "historical replay": ["neutral", "Historical"],
  "scenario suite": ["info", "Scenario suite"],
  scenario: ["info", "Scenario"],
  "feed backfill": ["warn", "Feed · backfill"],
  "forward test": ["up", "Feed · forward"],
};
export function EvidenceBadge({ evidence }) {
  const [kind, label] = EVIDENCE[evidence] || ["neutral", evidence];
  return html`<span class=${`badge ${kind}`}>${label}</span>`;
}

export function Metric({ label, children, big = false }) {
  return html`<div class=${`metric ${big ? "big" : ""}`}><span class="label">${label}</span><span class="value">${children}</span></div>`;
}

export function Loading({ label = "Loading…" }) {
  return html`<p class="muted" role="status">${label}</p>`;
}

export function ErrorNote({ error }) {
  if (!error) return null;
  return html`<div class="notice bad" role="alert">${String(error.message || error)}</div>`;
}

export function Dialog({ title, onClose, children, footer }) {
  useEffect(() => {
    const onKey = (event) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return html`<div class="dialog-backdrop" onClick=${(event) => event.target === event.currentTarget && onClose()}>
    <div class="dialog" role="dialog" aria-modal="true" aria-label=${title}>
      <div class="row between"><h2 style="margin:0">${title}</h2>
        <button class="ghost" aria-label="Close" onClick=${onClose}><${Icon} name="close" /></button></div>
      ${children}
      ${footer && html`<div class="row" style="justify-content:flex-end">${footer}</div>`}
    </div></div>`;
}

// Data loading ---------------------------------------------------------------

export function useAsync(loader, deps) {
  const [state, setState] = useState({ loading: true, data: null, error: null });
  const reload = useCallback(() => {
    let live = true;
    setState((previous) => ({ ...previous, loading: true, error: null }));
    loader().then(
      (data) => live && setState({ loading: false, data, error: null }),
      (error) => live && setState({ loading: false, data: null, error }),
    );
    return () => { live = false; };
  }, deps);
  useEffect(reload, [reload]);
  return { ...state, reload };
}

// Charts ---------------------------------------------------------------------

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
export const seriesColor = (index) => cssVar(`--series-${(index % 4) + 1}`);
// Canvas cannot read CSS variables, so resolve "var(--name)" to a color.
const resolveColor = (value) => (typeof value === "string" && value.startsWith("var(")
  ? cssVar(value.slice(4, -1).trim()) : value);

function toSeconds(value) {
  const text = String(value);
  return Date.parse(text.length === 10 ? `${text}T00:00:00Z` : text) / 1000;
}

// A uPlot time-series chart. `series` is [{label, values, color?, dash?}];
// `bands` shades [startIndex, endIndex] spans; `markers` draws points.
export function TimeChart({ times, series, height = 280, yLabel = "", bands = [], markers = [] }) {
  const box = useRef(null);
  useEffect(() => {
    if (!box.current || !times?.length) return undefined;
    const axisColor = cssVar("--text-2"), gridColor = cssVar("--border");
    const data = [times.map(toSeconds), ...series.map((item) => item.values)];
    const shade = cssVar("--surface-sunk");
    const options = {
      width: box.current.clientWidth || 600, height,
      scales: { x: { time: true } },
      legend: { show: series.length > 1 },
      cursor: { points: { size: 6 } },
      axes: [
        { stroke: axisColor, grid: { stroke: gridColor, width: 1 }, ticks: { stroke: gridColor } },
        { stroke: axisColor, grid: { stroke: gridColor, width: 1 }, ticks: { stroke: gridColor }, label: yLabel, size: 60 },
      ],
      series: [{}, ...series.map((item, index) => ({
        label: item.label, stroke: resolveColor(item.color) || seriesColor(index), width: 1.8, dash: item.dash,
        points: { show: false }, spanGaps: true,
      }))],
      hooks: {
        drawClear: [(u) => {
          const ctx = u.ctx;
          ctx.save();
          ctx.fillStyle = shade;
          for (const [start, end] of bands) {
            const left = u.valToPos(data[0][start], "x", true);
            const right = u.valToPos(data[0][end], "x", true);
            ctx.fillRect(left, u.bbox.top, Math.max(1, right - left), u.bbox.height);
          }
          ctx.restore();
        }],
        draw: [(u) => {
          const ctx = u.ctx;
          ctx.save();
          for (const marker of markers) {
            const x = u.valToPos(toSeconds(marker.time), "x", true);
            const y = u.valToPos(marker.value, "y", true);
            ctx.fillStyle = marker.side === "buy" ? cssVar("--up") : cssVar("--down");
            ctx.beginPath();
            const size = 5 * devicePixelRatio;
            if (marker.side === "buy") { ctx.moveTo(x, y - size); ctx.lineTo(x - size, y + size); ctx.lineTo(x + size, y + size); }
            else { ctx.moveTo(x, y + size); ctx.lineTo(x - size, y - size); ctx.lineTo(x + size, y - size); }
            ctx.fill();
          }
          ctx.restore();
        }],
      },
    };
    const chart = new uPlot(options, data, box.current);
    const observer = new ResizeObserver(() => chart.setSize({ width: box.current.clientWidth, height }));
    observer.observe(box.current);
    return () => { observer.disconnect(); chart.destroy(); };
  }, [times, series, height, bands, markers]);
  if (!times?.length) return html`<p class="muted">No data to chart.</p>`;
  return html`<div class="chart-box" ref=${box} role="img" aria-label=${`Chart of ${series.map((item) => item.label).join(", ")}`}></div>`;
}

export { html, useEffect, useRef, useState, useCallback, useMemo };
