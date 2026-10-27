# ADR-0011: Local JSON API and frontend architecture for v0.1.0

- **Status:** Accepted
- **Date:** 2026-10-20
- **Deciders:** Barloom maintainers; implementation choice recorded for user review

## Context

Barloom's backend is reached through a CLI and a static HTML snapshot (`dashboard`). A v0.1.0 frontend needs:

- one place to list every experiment, suite, and scenario;
- live reads of analysis and timelines;
- the ability to start work such as running a suite.

The core has no runtime dependencies, runs on the user's laptop, and executes trusted local strategy code. The user plans to overhaul the visual design once the backend is settled, so the contract between the two halves matters more than the first screens.

## Decision

**A local web app served by `barloom serve`.** The frontend is static files packaged in `barloom/assets/app/` and served by a standard-library `ThreadingHTTPServer`. Installing Barloom is the only setup. Opening `http://127.0.0.1:8765/` starts the app.

**A versioned JSON API is the only contract.** The frontend never reads SQLite or files directly. Everything goes through `/api/v1/` ([reference](../api.md)). `barloom.api.Api` implements it independently of HTTP, so the CLI, tests, and future clients share one code path. Breaking changes require `/api/v2/`.

**A workspace layer.** `barloom.workspace.Workspace` scans a folder for databases, configs (recognized by shape), and scenarios. An optional `barloom-workspace.json` can name other subfolders. Items are addressed by workspace-relative paths, and any path that resolves outside the workspace is refused.

**Writes are jobs.** `POST /api/v1/jobs` queues an action:

- `run-static`
- `run-suite`
- `feed-batch`
- `retire-cell`
- `scenario-generate`, `scenario-transform`, `scenario-slice`

Each action's parameters are validated at submit time. One worker thread runs jobs in order, so two jobs never write one database concurrently. Clients poll `GET /api/v1/jobs/{id}`. Live Feed stays CLI-only: a session-long process does not fit a job queue.

**Local-only security.** Running a config executes strategy code, so the server must not be reachable from other machines or from web pages:

- it binds only to loopback addresses and refuses others at startup;
- every request's `Host` header must name that address, which blocks DNS rebinding;
- writes need a per-process token, which the server injects into the page it serves. They also need `application/json`, and any `Origin` header must be the server itself. Other sites cannot read the token or the API's responses, because no CORS headers are sent;
- responses set `no-store`, `nosniff`, a restrictive Content Security Policy for HTML, and `frame-ancestors 'none'`;
- errors return a message without a traceback. A failed job's own error, with a short trace, is visible to the local user through the job.

**Frontend stack.** Constraints: it ships as static files, needs no Node runtime or build step, works offline, and keeps the CSP above. The app is ES modules under `barloom/assets/app/js/`, written with Preact components and `htm` templates. Sparklines are plain SVG, and uPlot draws the time-series charts. The user approved vendoring both libraries; they are pinned, unmodified, with licenses and hashes in `barloom/assets/app/vendor/README.md`. Static files are served with fixed content types, because on Windows `mimetypes` can map `.js` to `text/plain`, which browsers refuse for modules.

## Alternatives considered

| Choice | Benefit | Cost or reason not chosen |
| --- | --- | --- |
| Extend the static `dashboard` snapshot | No server | Cannot list workspaces, refresh, or start jobs |
| FastAPI or Flask server | Routing, validation, OpenAPI | First runtime dependencies for the core; the API is small enough for the standard library |
| Desktop shell (Electron, Tauri) | Native window | Large toolchain and binaries for a local research tool |
| React or Vue with a Node build | Large ecosystem | A build toolchain for contributors; a reasonable later step if the UI grows |
| Notebook or Streamlit front end | Fast to prototype | Extra dependencies, and weaker control over the security boundary and job model |
| Read SQLite from the browser (WASM) | No API to maintain | Couples the UI to journal schemas and migrations |
| No token on writes, relying on loopback | Simpler | Any web page the user visits could POST to localhost and run strategy code |

## Consequences

The API is now a public surface: response shapes should change only additively within v1. The job queue is in memory, so history is lost when the server stops; results stay in the databases. Long Alpaca backfills block later jobs while they run. Reads that analyze large journals run on the request thread and may be slow for multi-year minute data; caching can come later if measurements show the need.
