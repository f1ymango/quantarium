# Roadmap: v0.6 to v1.0

- **Status:** Agreed with the maintainer on 2026-10-24
- **Date:** 2026-10-24
- **Decision record:** [ADR-0037](../adr/0037-referee-not-competitor.md)

## 1. Where Barloom is going

> Barloom is a simple, local tool that says honestly whether a trading idea, the user's or an AI agent's, holds up. Its simulator is cross-checked against established engines, and its evidence rules keep users from fooling themselves.

Barloom does not try to match the largest engines in breadth or speed. It aims to reach trustworthy results for the questions it covers, with simple configuration and AI assistance, and to say plainly what larger engines do that it does not. A user without financial training should get a result they can rely on, together with its limits.

Barloom stands on three pillars:

1. **A simulator that is checked:** cross-checked against an established engine, and strengthened where that affects a verdict.
2. **Evidence rules:** pre-registration, controls, used data, holdouts, variant counts, training cutoffs, disguised replays, ablations, and, from v0.7, statistics that account for every attempt.
3. **A safe harness for AI agents:** permissions, budgets, a sandbox, review, proposals, and a metered model gateway.

## 2. Principles

- **A referee, not a competitor** ([ADR-0037](../adr/0037-referee-not-competitor.md)). Other systems are judged by replaying their decisions on Barloom's engine.
- **No other engine in Barloom's process or dependencies.** The core stays standard-library only. An established engine runs only in its own environment, for the cross-check.
- **Evidence before features.** A feature that can change a verdict comes before one that only adds coverage.
- **Security rules apply to every step.** Each new surface gets misuse tests in `tests/security/` (`AGENTS.md`, section 7).
- **Private until v1.0.** The repository stays private. The license, packaging, and public documents come with v1.0.

## 3. Milestones

### v0.6 — The referee

Judge decisions made outside Barloom.

| Step | Contents |
| --- | --- |
| v0.6.0 | A decision file format: dated target weights or orders per symbol, with the source system, its version, any model and training cutoff, and content hashes. A validating reader, fuzzed like the v0.5 parsers. |
| v0.6.1 | Replay: a cell kind that takes a decision file instead of a script, running on Barloom's engine with its costs and portfolio controls. |
| v0.6.2 | The Lab for replayed decisions: controls, used data, and holdouts apply. A new evidence level marks verdicts that cannot be rerun on a disguised market or without their model. |
| v0.6.3 | The live referee: an outside agent is asked for each session's decision through a gateway that shows it only data up to that session, so look-ahead is impossible and disguised replays, runs without the model, and controls all apply. Built on the `ask` gateway's budgets and tape. |
| v0.6.4 | How to export decisions from other systems: documented examples, with no new dependency. |
| v0.6.5 | The app, the tutorial, and the handbook. |

### v0.7 — Evidence you can share

| Step | Contents |
| --- | --- |
| v0.7.0 | Lab statistics: a deflated Sharpe ratio and the probability of backtest overfitting, computed from the attempt and variant counts Barloom already records. |
| v0.7.1 | A shareable evidence report: one self-contained HTML file per verdict, with its pins, hashes, controls, statistics, and caveats, and a command that checks a report against its workspace. |
| v0.7.2 | Model costs: a price table for estimates, and per-day model budgets across cells. |

### v0.8 — A more accurate engine

The aim is to make Barloom's own engine more accurate. An established engine serves as the measuring instrument: it shows where Barloom's results are wrong, and confirms each fix.

| Step | Contents |
| --- | --- |
| v0.8.0 | A cross-check harness: shared scenarios, strategies written for both engines, aligned assumptions (fill timing, costs, whole shares), and a report comparing trades, equity, and metrics within stated tolerances. The reference engine runs in its own environment. |
| v0.8.1 | Fixes for every difference under the same assumptions, and a decision record for every difference that comes from a modelling choice. |
| v0.8.2 | Realism that affects verdicts: cost and slippage models, and memory use for long histories. |
| v0.8.3 | Performance, measured with the existing scale probes, and improved where it limits real use. |

The reference engine is **backtrader 1.9.78.123** ([ADR-0038](../adr/0038-backtrader-as-reference-engine.md)), installed only in its own environment. zipline-reloaded was the first choice but could not be installed cleanly on Python 3.13 or 3.14 on Windows. zipline (on Python 3.12) or Lean remain options for a second check later.

### v0.9 — Compatibility with the user's own data

Barloom supplies **compatibility, not data**. It ships no real market data and licenses none: vendors' terms usually forbid redistribution, and the user chooses and pays for their own sources. Their quality, including survivorship bias, is the user's responsibility. Barloom's part is to make the user's data easy to bring in, and to say honestly what that data can and cannot support.

| Step | Contents |
| --- | --- |
| v0.9.0 | Barloom's documented research data format, with delisted securities and index membership where the user has them, imported into scenarios that record their source and a survivorship status the user declares. |
| v0.9.1 | The engine replays symbols that list and delist mid-run, and index membership as of each date. |
| v0.9.2 | The Lab uses the survivorship status, and flags universes chosen with hindsight. |
| v0.9.3 | The user's own trading history, from a generic trades CSV, as a decision file the Lab can judge. |
| v0.9.4 | Futu OpenAPI market data through a bridge in its own environment; Alpaca market data is already supported. Real-account checks are run by the maintainer. |
| v0.9.5 | The app and the handbook. |

Decided on 2026-10-26 ([plan](v0.9.x-data.md)): v0.9 adds brokerage **market data only**; paper trading moves to the post-v1.0 trading plan.

### v1.0 — Public release

- **The new name, Quantarium**, goes live: the project, the repository, the package, the `quantarium` command, the workspace folder, the environment variables, and the token prefix. Old Barloom workspaces stay readable. Until then the code and documents keep the name Barloom.
- **The license: PolyForm Noncommercial 1.0.0.** Anyone may use the project for themselves, for research, for teaching, and in noncommercial organizations. Commercial use is reserved for the copyright holders, the maintainer and Kandodaki.
- `SECURITY.md`, and contribution terms that let the copyright holders keep those rights for contributed code.
- A statement in the README that Barloom supplies no market data and gives no financial advice.
- Packaging for PyPI, and a README with screenshots.
- A decision on the watch list in `main`'s early history (left in place, or removed by a reviewed history rewrite).
- A security review for v1.0, covering everything since v0.5.
- Validation with real agent apps (Codex, OpenCode), run by the maintainer.
- A check before the repository goes public:
  - Kandodaki agrees to the license for code adapted from his work;
  - the `expectations-data` branch (market data saved from Yahoo Finance) and the ML branches are removed or kept private, since that data may not be redistributable;
  - the watch list in `main`'s early history is left or removed;
  - GitHub Actions stay off unless a reviewed workflow needs them.

## 4. Out of scope until after v1.0

- Matching established engines in speed or asset classes.
- Shorting, derivatives, intraday execution models, and markets outside US stocks.
- Placing real orders. The maintainer's long-term goal is for Barloom to trade on a user's own brokerage account exactly as a tested script did. That needs its own plan after v1.0, and a change to `AGENTS.md` section 1, which today says Barloom never places real orders. That plan would start with paper trading, and would keep two rules: every real order is confirmed by the user, and agents never place orders.

## 5. Decisions

Made with the maintainer on 2026-10-24:

| Topic | Decision |
| --- | --- |
| Direction | A referee, not a competing engine ([ADR-0037](../adr/0037-referee-not-competitor.md)) |
| Engine | Kept and made more accurate, measured against **backtrader** in its own environment (v0.8; changed from zipline-reloaded on 2026-10-26, ADR-0038) |
| Data | Barloom supplies **compatibility, not data**. Users bring their own data and are responsible for its quality, including survivorship bias. No real market data is shipped (v0.9) |
| Live Feed | Stays and will grow: brokerage connections for market data in v0.9; paper trading, then trading on the user's own account, after v1.0 under their own plan |
| Name | **Quantarium**, live from v1.0.0. The work up to then keeps the name Barloom |
| License | **PolyForm Noncommercial 1.0.0**, from v1.0.0. The repository has no license file until then |
| Repository | Stays private until v1.0.0 |

Still open:

1. **Brokerage connections for v0.9.** Research history and live market data are different needs; Barloom supports both but supplies neither:
   - Delisted securities and point-in-time index membership come from research data vendors, such as EODHD, Norgate, and Financial Modeling Prep. Most need a paid plan, and their index membership coverage varies.
   - Brokerage APIs (Alpaca, Futu OpenAPI, Interactive Brokers) supply current and recent prices and paper trading, and later real accounts, but limit history and rarely include delisted securities. Futu's history is rationed per stock per 7 days.
2. **Contributor terms** before outside contributions are accepted, so that contributed code does not limit the copyright holders' commercial rights.

## 6. Planning notes (2026-10-24)

How this roadmap came about, for whoever picks it up next.

- **Where Barloom stands.** Seen from outside, Barloom sits between projects that let AI agents trade (TradingAgents, ai-hedge-fund, FinRL), which seldom judge their results, and backtesting engines (Lean, NautilusTrader, vectorbt, zipline-reloaded), which are far ahead on speed and breadth. Its closest neighbours are small MCP servers that wrap a backtester. No project found combines evidence rules, a contained agent harness, and a local app.
- **What the maintainer wants.** A simple tool that gives users without financial training a result they can rely on, with or without AI help. In the maintainer's words, it is like tuning a mid-range computer to do what an expensive one does on an ordinary day: within its limits, and honest about them.
- **Why cross-check the engine.** Matching another engine's numbers proves little by itself. Under the same assumptions the results should agree, and any difference is a bug. Under different assumptions they should differ in an explained way. The cross-check is the measuring instrument for making Barloom's engine more accurate.
- **Why no bundled data.** Vendors' terms usually forbid redistribution, the maintainer does not buy data, and scenarios already let users build market history from their own data.
- **Real trading, later.** `AGENTS.md` section 1 says Barloom never places real orders. Trading on a user's own account needs that rule changed by the maintainer, and a plan that starts with paper trading, has the user confirm every real order, and never lets agents place orders. AI agents working on Barloom never place trades or use real accounts themselves.
- **Before going public.** The v1.0 checklist above includes Kandodaki's agreement to the license, branches that hold saved market data, the watch list in early history, GitHub Actions, the rename, and the no-data and no-advice statement.
- **Housekeeping done the same day.** `main` is branch-protected (pull requests with one approval for collaborators; no force pushes or deletion; the maintainer bypasses). PR #2 was closed unmerged and kept as reference. The stale `desk-plan` branch was deleted. The maintainer turned GitHub Actions off on 2026-10-25, and closed PR #1 the same day.

The next step is the detailed plan for v0.6, the referee.
