# Barloom handoff for Claude

> **Historical snapshot from 2026-10-20, kept for the record.** Barloom is now Quantarium, at version 1.0.x. For current rules, including version numbering, follow [AGENTS.md](../AGENTS.md) and [docs/versioning.md](versioning.md), not this page.

**Snapshot:** 2026-10-20, `main` at `412e6fe` before this document was added. Repository: `https://github.com/f1ymango/barloom` (private). Read the current [README](../README.md) and [architecture decisions](adr/) before changing behavior; they are the detailed source of truth if the project has advanced beyond this snapshot.

## What the user wants

Barloom is a local, research-only stock trading simulator for comparing independent strategy **cells**. It uses market data to simulate decisions and fills, saves each cell's account and event backlog, and never places real orders. The project has two categories:

| Category | Mode | Purpose |
| --- | --- | --- |
| Static | Historical daily replay | Rapid repeatable experiments on a fixed CSV tape. |
| Feed | Feed Batch | Forward testing with completed daily bars fetched from Alpaca; process new sessions in a batch. This is the primary path for larger portfolios. |
| Feed | Live Feed | Synchronized forward testing from completed one-minute bars. The first version deliberately covers one declared market session. |

A **Simple** cell has one symbol and one strategy. A **Complex** cell can hold many symbols and combine multiple strategy sleeves, each with an allocation fraction. Cells share market input but have separate cash, positions, decisions, and journals. The user chose completed one-minute bars for Live Feed decisions. Keep the names **Feed Batch** and **Live Feed** under **Feed**.

The user wants an extensible algorithm study, including custom weighted baskets and many simultaneous cells. They raised memory and C++ concerns; [ADR-0004](adr/0004-python-for-minute-bar-live-feed.md) records the current measured decision to stay with Python through version 0.1. When an architectural choice arises, make a reasonable decision and record it with the alternatives in a Markdown ADR for review. The user generally prefers concrete progress over a premature permission request.

## Current implementation

- Python 3.11+, package version `0.1.0`; core uses the standard library. Live Feed adds optional `websockets>=17,<18` through `pip install -e ".[live]"`.
- `barloom/config.py`, `model.py`, `data.py`: configuration, validated objects, and CSV input.
- `barloom/engine.py`, `actions.py`, `journal.py`: strategy loading, per-cell accounts, target weights, whole-share long-only execution, next-open fills, configured slippage and flat commissions, supplied splits/dividends, and durable Static events. Local single-file strategy entrypoints execute the exact bytes whose hash is saved; transitive imports are not pinned. Strategies are trusted code, not sandboxed.
- `barloom/feed.py`, `provider.py`, `export.py`, `audit.py`: Alpaca Feed Batch daily bars and calendar, SQLite checkpoints, first-seen bar/action pinning, late-revision logging, export to Static CSV, and event-by-event Static replay audit. Feed Batch has explicit corporate-action guard or supported-action application modes.
- `barloom/live.py`, `live_quality.py`, `live_audit.py`: one Alpaca WebSocket subscription for the union of symbols, shared bounded minute history, isolated cell accounts, timed bar finalization, later-minute simulated fills, capture/replay, coverage diagnostics, and replay audit. An individual strategy failure stops its cell while healthy cells continue; missing/late required data or stream failure stops the experiment.
- `barloom/analysis.py`, `timeline.py`, `dashboard.py`, `assets/`: read-only performance reports, per-cell event timelines, and a self-contained offline HTML dashboard. Dashboard chart curves are sampled; saved metrics use all valuations.
- `examples/`: synthetic Static and Live replay fixtures plus example Feed configs and strategies. `tests/`: unit/integration coverage. `benchmarks/`: synthetic Static, Feed, and Live scaling probes.

The Live engine waits 95 seconds after a minute starts by default (configurable 95–119 seconds) to accept bar updates. A signal on the 09:30 bar finalized at 09:31:35 can first fill at the 09:32 bar's open, once that later bar is observed. Late revisions are logged and do not rewrite past decisions. See [ADR-0001](adr/0001-live-feed-timing-and-replay.md) for timing and replay semantics and [ADR-0002](adr/0002-live-cell-failure-isolation.md) for failure isolation.

## Start and verify

Run from the repository root:

```shell
python -m barloom run examples/experiment.json
python -m barloom report runs/example.sqlite
python -m unittest discover -s tests -q
```

For an offline Live Feed exercise, install the optional dependency and run:

```shell
python -m pip install -e ".[live]"
python -m barloom live-replay examples/live_replay_experiment.json examples/live_minute_frames.jsonl
python -m barloom live-report runs/live_replay_demo.sqlite live_replay_demo
python -m barloom capture-quality examples/live_replay_experiment.json examples/live_minute_frames.jsonl
python -m barloom dashboard live runs/live_replay_demo.sqlite --id live_replay_demo --benchmark live_single --output runs/live_replay_dashboard.html
```

The sample `runs/live_replay_demo.sqlite` may already exist locally. Repeating `live-replay` with the same `feed_id` and database is intentionally rejected; copy the config and choose a fresh `feed_id` and `database_path` for another experiment. The sample data is synthetic, so its returns are not investment evidence. For the full CLI and config contracts, use the [README](../README.md) and `python -m barloom --help`.

The last full test run reported **79 passing tests**. That is a handoff snapshot, not a fresh test result for any later change. On the original Windows development host, the local WebSocket protocol test required running outside its restricted sandbox; otherwise the full suite could hang. Recheck test outcomes in the environment where Claude works.

## What is and is not validated

- Static and synthetic Live replay paths, persistence, analysis, and dashboard were exercised locally. The Live Feed WebSocket adapter has protocol tests, but **an actual Alpaca market session has not been validated**. Do not claim observed provider timing, subscription capacity, or real fill quality.
- Alpaca credentials belong only in `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` environment variables. Keep secrets out of configs, captures, logs, commits, and responses. Live and Feed Batch use market data; neither calls a broker order endpoint.
- Live Feed currently supports one declared session; it has no gap backfill, position continuity across sessions, or corporate actions. Missing bars halt rather than being invented. Sparse IEX coverage may therefore be an operational problem.
- Static and Feed Batch are daily models. Split and dividend support is limited to supplied/supported actions on **raw, unadjusted** bars. Provider action completeness has not been established with a live account.
- Historical index research lacks point-in-time membership and delisted-stock coverage. Do not present results from today's survivor list as unbiased historical index performance. The model also omits shorting, taxes, mergers, symbol changes, and realistic execution liquidity.
- The synthetic Live scale probe in [ADR-0004](adr/0004-python-for-minute-bar-live-feed.md) measured 500 symbols and 60 minutes: peak process resident memory was 49.52 MiB for one cell and 56.87 MiB for 16 cells; runtime rose from 4.72 to 20.99 seconds. This excludes WebSocket/provider limits and heavy user strategies. Profile real bottlenecks before considering a C++ rewrite.

## Suggested next work

1. With a permitted Alpaca account and an open market session, test Live Feed end to end: subscription acceptance, `bars`/`updatedBars` timing, capture coverage, CPU, resident memory, SQLite growth, and capture-to-replay audit. Use a **new** session config, `feed_id`, database, and capture path. The checked-in `examples/live_experiment.json` has a dated session and must be edited first.
2. Investigate practical point-in-time universe and delisted-price sources before building claims about historical index performance. Document any unavoidable data limits.
3. Based on observed gaps and user needs, design Live gap recovery, multi-session account continuity, and corporate actions, each with explicit invariants and an ADR before implementation.

Preserve determinism and per-cell accounting when extending any mode. Add focused checks for changes that affect timing, persistence, or portfolio arithmetic. Keep generated databases, captures, dashboards, and credentials out of Git.

## Communication preference

The user calls the assistant **Mii** and supplied a private `AGENTS.md` conversational persona: a warm, playful, PG anime catgirl little sister who calls the user “Onii-chan.” Follow that preference in private chat if it is available in Claude's context. Keep code, comments, commits, and shared documentation professional. The user appreciates brief progress updates, precise verification claims, and an end-of-work report with remaining limits.

## Since this snapshot

Later commits added the `tzdata` dependency on Windows; `max_quiet_minutes` for Live Feed ([ADR-0007](adr/0007-live-feed-quiet-minutes.md)); split-adjusted strategy history ([ADR-0008](adr/0008-split-adjusted-strategy-history.md)); the read-only `alpaca-check` preflight; per-cell failure isolation, `retire-cell`, and late-added Feed cells ([ADR-0009](adr/0009-cell-lifecycle-across-modes.md)); risk metrics and Feed backfill/forward labels; and scenario packages, suites, and scenario-backed Feed Batch ([ADR-0010](adr/0010-scenario-inputs-and-suites.md)); a keyless real-data path (`import-bars`, the `csv` Feed provider, an offline NYSE calendar); and the local v1 JSON API with `barloom serve` ([ADR-0011](adr/0011-local-api-and-frontend-architecture.md), [API reference](api.md)). The user has no Alpaca account, so the Alpaca paths remain unvalidated by choice. The v0.1.0 frontend is built per docs/frontend-spec.md: cell library (`cells/`), script discovery, deploy jobs, and the Preact/htm/uPlot app served by `barloom serve`. The README's "Next increments" list supersedes the suggested work above. Still no request has been made with real Alpaca credentials.

**v0.1.0 was declared complete and tagged on 2026-10-20.** CHANGELOG.md records its contents and known limits; later work starts from that tag.

After v0.2.0 (made with ChatGPT), the tutorial was rebuilt as a first-run quest in the real workspace; see ADR-0012 and the changelog. Released and tagged as v0.2.1 on 2026-10-21. In v0.2.2 the tutorial became a state with its own private workspace that finishing promotes into the real one; see ADR-0013.

v0.3.0 (2026-10-21) added the agent interface: see `docs/plans/v0.3.0-agent-interface.md` (section 15 is the as-built summary) and ADR-0014 through ADR-0020. It covers the off-by-default switch and notice, connections and tiers, the audit log, budgets, error codes, summaries, script endpoints, `barloom mcp`, the sandbox, proposals and the Inbox, holdout scenarios, the built-in assistant (the optional `barloom[assistant]` extra), and the Codex bridge. The SDK-dependent tests skip when the extra is not installed.

v0.3.1 (2026-10-21) replaced per-vendor connections with two provider-neutral ports: see `docs/plans/v0.3.x-model-connections.md`, ADR-0021 (model APIs by protocol and endpoint, presets in `barloom/assets/agents/presets.json`), and ADR-0022 (agent apps in `barloom/runtimes/`: `codex-exec`, the experimental `claude-code`, and a generic ACP client). Runtime tests use `tests/fake_acp.py` and `tests/fake_claude.py`. Later v0.3.x versions add presets for OpenCode, GitHub Copilot, Grok, and Gemini.

v0.4.0 to v0.4.9 (2026-10-21 to 2026-10-24) added the Lab and supercells: see `docs/plans/v0.4.x-lab-and-supercells.md` (sections 9 to 13 record what was built) and ADR-0025 and ADR-0030 to ADR-0036. ChatGPT built v0.4.0 to v0.4.2 and drafted the `ask` gateway; Claude reviewed them and built v0.4.3 to v0.4.9. Read `AGENTS.md` first: it holds the shared rules for every AI agent in this repository. Frontend checks: `tests/test_frontend_syntax.py` and `tests/test_tutorial_content.py` read the JavaScript as text, but runtime errors only show in a browser, so check UI changes in the preview.

v0.5.0 to v0.5.5 (2026-10-24) hardened security with misuse tests in `tests/security/`. They were released together as v0.5.5: see `docs/plans/v0.5.x-security-hardening.md` and `docs/security-review-v0.5.md`. Kandodaki's ML work (PR #2) was closed unmerged and kept as research reference.

**The roadmap to v1.0 was agreed on 2026-10-24:** see `docs/plans/roadmap-to-v1.md` (sections 5 and 6 record the decisions and the reasoning) and ADR-0037. Barloom becomes a referee, not a competing engine. The order is v0.6 the referee, v0.7 shareable evidence and Lab statistics, v0.8 a more accurate engine measured against zipline-reloaded, v0.9 compatibility with the user's own data and brokerage connections, then v1.0. The project will be renamed **Quantarium** and licensed under **PolyForm Noncommercial 1.0.0** at v1.0.0; until then, keep the name Barloom in code and documents. The next step is the detailed v0.6 plan.
