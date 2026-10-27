# Frontend v0.1.0 design (historical)

- **Status:** Historical design record for v0.1.0; current behavior has since changed.
- **Date:** 2026-10-20
- **Builds on:** [ADR-0011](adr/0011-local-api-and-frontend-architecture.md) (local web app over the v1 API) and [docs/api.md](api.md)

> This document records the initial design of v0.1.0, a private release. For current behavior, use the README, the Learn handbook in the app, and the latest release plan.

## 1. Concept

Quantarium's home screen is a **collection of cells**: small cards, each one an algorithm with its own account, gathered like a team of creatures. A new cell starts as an empty "Unnamed cell" card. Opening it is where the user defines what it trades and how it decides. A cell can then be **deployed** to be tested: replayed on history or scenarios (Static), or followed forward as new data arrives (Feed). Its card always shows how it is doing.

The frontend never invents results. Every number comes from the v1 API, and every view states what kind of evidence it shows: historical replay, synthetic scenario, backfill, or forward test.

### Vocabulary shown to users

| Term | Meaning | Code name |
| --- | --- | --- |
| Cell | One algorithm with its own simulated account | cell |
| Simple / Complex | One stock and one script, or many stocks and scripts | `mode` |
| Script | A strategy file the cell runs | strategy |
| Sleeve | One script in a Complex cell, with its share of the money | strategy with `allocation` |
| Holdings | The stocks the cell may hold | `symbols` |
| Deployment | A cell placed into a Static run, suite, or Feed experiment | run or experiment cell |
| Scenario | A named market condition to test against | scenario |

## 2. Navigation shell

A left sidebar, expanded at 232 px with icon and label, or collapsed at 64 px with icons only and labels as tooltips. The toggle is at the sidebar's bottom, and the choice is remembered in the browser. Below 768 px wide, the sidebar becomes a slide-over opened from a top-left button.

| Item | Page | Purpose |
| --- | --- | --- |
| Home | Home | The whole cell collection; the first screen |
| Feed | The arena | Cells being forward-tested, as cards grouped by experiment |
| Static | The training ground | Cells with replay and scenario results, as cards |
| Scenarios | Scenarios | Browse, generate, and transform market conditions |
| Log | Log | Every run and experiment, with a Feed / Static switch |
| Activity | Activity | Job queue and history |
| Workspace | Workspace | Data files, folders, server info |

Feed sits above Static because forward testing, and later real data connections, are where Quantarium is headed. Pages are organized around cells; the Log keeps the experiment-by-experiment record.

A header strip on every page shows the page title, the workspace name, and a job indicator: a spinner with a count while jobs run, linking to Activity.

## 3. Home: the cell collection

### Empty state
A centered illustration, the line "No cells yet", one sentence explaining what a cell is, and a primary **+ Add cell** button.

### With cells
- **+ Add cell** stays in the top bar, top right, as the primary action. Clicking it creates an "Unnamed cell" card immediately, places it first, and opens it.
- **Manage**, beside it, switches the grid into selection mode: each card shows a checkbox, and a bar offers **Duplicate**, **Archive**, **Restore** (for archived cells), and **Delete** (only for cells with no deployments). **Done** leaves the mode. A **Show archived** toggle sits in the bar.
- Cards sit in a responsive grid: 4 across on a wide desktop, 2 on a tablet, 1 on a phone.
- Filter chips: All, Simple, Complex, Drafts, Deployed. Also a text search by name and a sort (recently edited, name, best return).

### Cell card
Roughly 240 × 300 px, with a playful structure but no invented data:

1. **Header:** name, or "Unnamed cell" in muted italics; a mode badge (Simple or Complex), whose color acts as the cell's "type"; and a status dot.
2. **Portrait:** a sparkline of the latest deployment's equity, or for a draft, a dashed outline and the text "Not configured".
3. **Stats block**, like a creature's stat sheet, from the most recent completed result:
   - Return
   - Max drawdown
   - Sharpe
   - Sessions
   
   Each row has a small bar against the collection's range. If there is no result yet: "No results yet".
4. **Footer:** holdings count, sleeve count, and deployment badges (Static, Suite, Feed) with counts.

Status dot meanings:

| Dot | Meaning |
| --- | --- |
| Grey | Draft, incomplete |
| Blue | Configured, never run |
| Green | Has results |
| Amber | Running |
| Red | Its latest deployment failed |

The card's menu (⋯) offers: Open, Duplicate, Rename, Archive. Deleting a cell that has results is not offered; archive it instead.

## 4. Cell page

Reached by clicking a card; the URL is `#/cells/{id}`.

### Header
- An inline-editable name, the mode badge, and a version tag (§6).
- Primary action **Run**, opening the deploy dialog (§5). Secondary: Duplicate, Archive.
- A **result picker** chooses which deployment the page shows. The default is the most recent. Each option carries a provenance label: Historical, Synthetic scenario, Transformed scenario, Feed (backfill), or Feed (forward).

### Tabs
**Overview** (default once configured) · **Configure** (default for drafts) · **Runs** · **Timeline**

### Overview: the center view adapts to size

The center shows the cell's market picture. How much detail fits depends on how many things it must show at once. Visual working memory holds about **four** items (Cowan, 2001). That matches the practical limit of about four or five distinguishable lines in one chart. So:

| Holdings | Center view | Content |
| --- | --- | --- |
| **1 to 4** | **Chart** | One price line per stock, buy and sell markers from the cell's fills, and the cell's equity as a second panel below on a shared time axis. With one stock, a candlestick is offered. |
| **5 to 30** | **Tiles** | One tile per holding: symbol, a big signed % change over the selected range (green or red with a ▲/▼ glyph, never color alone), current weight, value, and a tiny sparkline. Sort by weight, return, or name. 30 tiles, about 6 × 5, fit one desktop screen. |
| **31 or more** | **Aggregate dashboard** | KPI row: equity, return, max drawdown, holdings count, % of holdings up. Below it: a histogram of holding returns, top 5 and bottom 5 contributors, concentration (largest weight, top-10 share), cash share over time, and a searchable table of all holdings. |

The selected range applies everywhere: the whole run, or 1M, 3M, 6M, 1Y.

Above the center view, in every size class, a **summary strip** shows the cell's own result:
- equity sparkline
- return, as a big signed number
- max drawdown
- volatility
- Sharpe
- turnover
- exposure
- excess return against the chosen benchmark, if any

Sleeves have their own panel, right of the center view on desktop and below it on narrow screens:

| Sleeves | Panel |
| --- | --- |
| 1 to 4 | A stacked allocation bar with each sleeve's script name and share, then each sleeve's latest target weights |
| 5 or more | A table of script, allocation, parameters, and latest target count |

Per-sleeve profit and loss is not available yet: the engine pools sleeves into one account. The panel says so instead of estimating it.

### Configure
A form whose live summary card mirrors the cell card.

- **Name** (required, unique).
- **Mode:**
  - Simple: one stock and one script at 100%.
  - Complex: many stocks and scripts.
  
  Switching from Complex to Simple is allowed only when that fits, with the reason shown otherwise.
- **Holdings:** a chip input for symbols, with paste-a-list support and a duplicate check. A hint warns when the list is over 30, and over 500.
- **Starting cash**.
- **Scripts:** a picker listing the scripts found in the workspace (§7). Each sleeve has an allocation slider, and allocations must total at most 100%; the remainder is labeled "kept as cash". Parameter fields come from the script's constructor defaults.
- **Rules,** collapsed under "Portfolio rules":
  - rebalance every N sessions
  - maximum weight per stock
  - minimum cash
  - previous-volume participation cap
  
  Each rule has a one-line explanation.

Validation messages follow the backend's wording. **Save** writes the cell; nothing is deployed implicitly.

### Runs
A list of every deployment of this cell (date, kind, data or scenario, status, return) with a link into the corresponding page.

### Timeline
A day-by-day or minute-by-minute event list from the timeline API (valuations, signals, orders, corporate actions, failure or retirement), filterable by date range and event kind, with CSV export.

## 5. Deploy dialog

From a cell's **Run** button. Choose one:

1. **Replay on data:** pick a price file from the workspace, either a Static CSV or an `import-bars` output, then costs (slippage, commission). Runs a Static job.
2. **Test across scenarios:** pick scenarios (multi-select gallery) and an optional benchmark cell. Runs a suite of this cell (plus benchmark). `symbol_map` is filled automatically when a scenario has as many symbols as the cell; otherwise the user maps them.
3. **Forward test:** pick a Feed experiment to join, or create one. Its source is a `csv` file, a scenario, or Alpaca if credentials exist. The cell joins after the last processed session (ADR-0009). The dialog states that the cell's configuration is frozen from now on.

Each option shows what will happen in one sentence before the user confirms, then submits a job and shows its progress inline.

## 6. Cell definitions and versions

Cells are stored as files in the workspace, `cells/{id}.json`: a stable `id`, a `name`, the cell specification the engine already accepts, and timestamps. Editing a cell that has never been deployed updates it in place. Once a cell has results, saving changes creates a **new version** (v2, v3…), and earlier results stay attached to the version that produced them. The cell page shows the latest version, with a version menu. A Feed deployment pins the exact version, matching Feed Batch's pinning rules.

Deployments record the cell's `id` and version in run metadata, so the Runs tab can find every result of a cell across databases.

## 7. Other pages

**Feed: the arena.** One section per Feed Batch experiment: its name, its source (shown as "practice · scenario" when it reads a scenario), and its last session, with **Advance** and a link to its Log entry. Each of its cells is a card that leads with **forward return**, or practice return for a scenario, plus an equity sparkline, forward and backfill session counts, drawdown, Sharpe, status, and **Retire**. **Advance all** advances every active experiment. **Import from Static** picks one of your cells, showing its Static results, and opens the Run dialog limited to Forward test: join an experiment or start one. Clicking a card opens the cell's page, or the experiment's Log entry for cells made outside the library.

**Static: the training ground.** Cards for cells with replay or suite results, each showing its most recent Static result, then a "Ready to train" group of configured cells without one. **Test a cell** picks a cell and opens the Run dialog limited to scenarios and replay.

**Log.** A Feed / Static switch over the lists that the Static and Feed pages first showed. Feed lists Feed Batch experiments and read-only Live Feed results. Static lists Runs (Static runs with their cells and data file) and Suites. A suite opens to its **matrix**: cells as rows, scenarios as columns, with a metric selector (return, max drawdown, volatility, Sharpe, excess). Cells are colored on a diverging scale with signed text, and there's a per-tag average row. Caveats show above the matrix. Clicking a cell in the matrix opens that cell's Overview on that run.

**Scenarios.** A gallery of scenario cards. Each card shows a mini closing-price chart per symbol, a kind badge (historical, synthetic, transformed), tags, and its date span. Actions: **Generate** (a form for segments, symbols, and seed, with a live preview after generating), **Transform** (shock, volatility, drift on a chosen parent), and **Slice** (from an imported data file). Provenance is shown on the detail page.

**Log entries for Feed experiments.** Each experiment page shows:
- its cells' equity curves, with backfill sessions shaded and the forward part emphasized;
- forward-only return beside total return;
- an **Advance** button that submits `feed-batch`;
- per-cell retire.

Live Feed experiments are listed read-only, with their minute curves.

**Activity.** Jobs, newest first, with status, duration, and a link to the result or error. A failed job shows its message and a collapsible trace.

**Workspace.** The server URL, the folders scanned, the databases with sizes, and the imported data files with their manifests.

## 8. Visual language

- **Light and dark themes,** following the system, with a manual override.
- **Restrained color:** one accent for primary actions. Data colors come from an accessible palette with at most 8 categorical colors and a red–green diverging scale for returns. Every gain or loss also carries ▲/▼ and a sign, so meaning never depends on color.
- **Mode colors:** Simple and Complex are the cells' "types", each with a distinct, colorblind-safe hue. They are used only on badges and card borders.
- **Typography:** a system font stack; tabular figures for every number; returns shown as `+12.3%`, with drawdowns positive and labeled.
- **Motion:** card hover lift and tab transitions under 150 ms, disabled when the user prefers reduced motion.
- **Accessibility:** WCAG 2.1 AA contrast, full keyboard navigation (the grid is arrow-key navigable, Enter opens), visible focus, and labeled charts with a data-table fallback.

## 9. Backend additions required

| Addition | Why |
| --- | --- |
| Cell library: `cells/` folder; API `GET cells`, `GET cells/{id}`, and jobs `cell-create`, `cell-save`, `cell-archive` | Cells exist before any deployment |
| Script discovery: `GET scripts`, listing strategy classes and constructor parameters found by **parsing** workspace `.py` files, not importing them | Script picker without executing code |
| Deploy jobs: `deploy-static`, `deploy-suite`, `deploy-feed`, which write generated configs under `configs/generated/` and run them | Deploy dialog |
| Run metadata `cell_id` and `cell_version` on every deployment; `GET cells/{id}/runs` | Runs tab and card stats |
| Holdings series for the center view: per-symbol price series and fill markers for a cell's run (`GET holdings`) | Chart and Tiles views |
| Data-file listing in the workspace overview (imported CSVs and manifests) | Deploy dialog and Workspace page |

## 10. Scope for v0.1.0

**In:**
- the shell
- Home with cards
- the Cell page (Overview with all three size classes, Configure, Runs, Timeline)
- the deploy dialog (all three paths)
- the Static page with the suite matrix
- the Scenarios gallery with generate and transform
- the Feed page with Advance and retire
- Activity
- light and dark themes

**Later:**
- per-sleeve profit and loss, which needs an engine change
- a strategy code editor
- the Live Feed control panel
- drag-to-reorder cards
- sharing or exporting cards
- multi-workspace switching

## 11. Open questions

1. Should a card's stats come from its most recent result, or from a result the user pins?
2. Should the Feed page let users create a new `csv` Feed experiment from scratch, or only through a cell's deploy dialog?
