# Changelog

## v1.0.10 — 2026-10-29

Harden the Futu market-data bridge.

- Resolve `localhost` to `127.0.0.1` before connecting to OpenD, and reject invalid ports.
- Restrict output paths inside the checkout to the local `data/`, `scenarios/`, and `private/` folders after resolving paths and links.
- Refuse append with forward-adjusted history, whose earlier prices can change after corporate actions. Document OpenD's local-only settings and the SDK log location.
- Add fake-data misuse tests for connection and output boundaries. No real Futu account was used in development or tests.

## v1.0.9 — 2026-10-28

State the data boundary explicitly.

- The public repository provides compatibility code, data formats, and invented examples, never usable real market data. Provider-sourced prices and datasets derived from them remain local to each user and are not committed.
- The Futu bridge guide clarifies that users obtain access through their own accounts and check their provider's data terms. It also distinguishes fake-candle automated tests from the maintainer's first real check.

**Results change:** None.

## v1.0.8 — 2026-10-28

One name everywhere.

- Every document, including the changelog, decision records, plans, and security reviews, uses the name Quantarium. Passages about the 1.0 rename now speak of names used before 1.0, and the README's upgrade note is gone. The earlier name remains only inside the compatibility code that reads older workspaces, as `docs/versioning.md` promises for 1.x.
- `.gitattributes` again marks the vendored frontend libraries (its path still pointed at the folder's pre-1.0 location), and the benchmarks import `quantarium` directly.
- `tests/test_branding.py` checks that no document or app asset uses the earlier name.

**Results change:** None.

## v1.0.7 — 2026-10-28

A logo.

- Quantarium's mark is a Q around three candlesticks, its tail a check mark: the referee that checks whether a result holds up. It replaces the plain "Q" tile in the app's sidebar and on the tutorial's title screen, becomes the browser tab's icon, and heads the README (`quantarium/assets/app/logo.svg`, `docs/images/logo.svg` and `logo.png`).
- `tests/test_branding.py` checks that the served SVG holds no script, event handler, or external reference.

**Results change:** None.

## v1.0.6 — 2026-10-27

Review of v1.0.5 and the final check before the public release.

- **Reviewed v1.0.5** (Codex's tutorial and handbook catch-up): read in full, scanned for raw HTML, network calls, and process launches (none), and walked through the six-chapter tutorial from first click to finish in a fresh workspace. Finishing adds one cell with its market, Static result, and Feed practice; the Lab practice is discarded (no Lab records or Lab database in the workspace afterwards); no console errors.
- **Fix:** during the tutorial, the Lab form's idea ID is read-only. The tutorial follows its two Lab attempts by that ID, so changing it would have left the chapter waiting.
- **Final check:** every page and cell tab in a populated workspace showed no errors and no `undefined`, `NaN`, or leftover old name; the light theme and a 1,024-pixel window lay out without sideways scrolling; controls are labelled and show a focus outline. Tracked files hold no personal paths or emails, no workflows, and only invented market data.

**Results change:** None.

## v1.0.5 — 2026-10-27

UI tutorial catch-up before the public release.

- The guided tutorial adds a Lab chapter: pre-register a cell against its market, read its controls and caveats, then see a second attempt on reused data marked exploratory.
- The tutorial's two Lab attempts stay in its private workspace and are discarded on finish. The tutorial still promotes one working cell with its market, Static result, and Feed practice.
- The handbook numbers the Lab as chapter 7 and the following chapters as 8–16. Learn describes all six guided chapters.
- **Do it for me** waits for the highlighted control and reports an error if the control never appears or is unavailable, instead of silently leaving the tutorial stuck.
- The Lab waits for its ledger, cells, and workspace before showing registration or an empty state. The README now describes the six-chapter tutorial and the private Lab practice accurately.
- The v0.1 frontend design is labeled as a historical record.

**Results change:** None.

## v1.0.4 — 2026-10-26

_Committed as 1.0.0rc4 and tagged v1.0.4; see [versioning](docs/versioning.md). A later commit set the version to 1.0.4 everywhere and added the versioning rules and `tests/test_version.py`, which keeps the version the same in every file._

The security review of everything since v0.5 ([docs/security-review-v1.0.md](docs/security-review-v1.0.md)). Each fix has a test that fails without it.

- **Evidence reports no longer reveal this computer's paths.** Records of cells using decision files, inboxes, deciders, or model access hold absolute paths, which a shared report showed. Reports now write `<workspace>` and `~`; `verify-report` applies the same rule to the ledger and still accepts earlier reports.
- **Decider replies are read in bounded pieces**, so a reply that never ends its line is refused without filling memory.
- **The compatibility layer renames only whole pre-1.0 `module:Class` entrypoints**, not strategy parameters that merely start with the earlier package name.
- **Converting a trades file no longer replaces an existing decision file**, which a Lab registration may have pinned; the API answers `409 already_exists` unless asked to overwrite.
- `AGENTS.md`: the pre-commit scan also looks for Quantarium's own token prefixes, `qtm_` and the pre-1.0 `blm_`, and the review joins the invariants' references.

## v1.0.3 — 2026-10-26

_Committed as 1.0.0rc3 and tagged v1.0.3; see [versioning](docs/versioning.md)._

Packaging.

- Package metadata for PyPI: the license as an SPDX expression (`PolyForm-Noncommercial-1.0.0`) with `LICENSE.md` and `NOTICE` shipped in the wheel, authors, keywords, classifiers, project links, and the README as the description. The build needs setuptools 77 or newer.
- The wheel holds only `quantarium/` and the two-file alias package for imports written before 1.0; the source archive leaves out the tests (`MANIFEST.in`). A stale `build/` folder from an older build had been copying 63 old files into the wheel; the release steps now remove it first ([docs/releasing.md](docs/releasing.md)).
- Checked by installing the wheel and `tzdata` into an empty environment and running it from outside the repository: the command line, a Static run, a data check, a dashboard, the import alias, and the app served from the installed package.
- The README's images use absolute links to the public repository, so they show on PyPI.

## v1.0.2 — 2026-10-26

_Committed as 1.0.0rc2 and tagged v1.0.2; see [versioning](docs/versioning.md)._

The license and the documents a stranger reads first.

- `LICENSE.md`: the PolyForm Noncommercial License 1.0.0, copied byte for byte from the PolyForm Project's repository (tag `1.0.0`). `NOTICE` holds the license's required notice naming the copyright holders, f1ymango and Kandodaki, and says that commercial use needs a separate license.
- `SECURITY.md` (private reporting through GitHub, what counts, what is out of scope) and `CONTRIBUTING.md` (ideas and reports welcome; code is not merged as it is, so no contributor agreement is needed yet).
- The README opens with what Quantarium is and is not, a statement that it supplies no market data and gives no financial advice, installation from PyPI or a clone, and three screenshots of an invented demo workspace (`docs/images/`). Outdated lines about delistings and index membership now describe imported research data; "Next increments" becomes "After 1.0"; new sections on upgrading from Quantarium and on the license.
- `quantarium --version`.

## v1.0.1 — 2026-10-26

_Committed as 1.0.0rc1 and tagged v1.0.1; see [versioning](docs/versioning.md)._

The project takes its public name, **Quantarium** ([plan](docs/plans/v1.0-release.md)).

- The package is `quantarium`, with the `quantarium` command and `python -m quantarium`; the app, the MCP server, agent tokens (`qtm_`), the browser token header, environment variables (`QUANTARIUM_*`), the workspace folder (`.quantarium`) and file (`quantarium-workspace.json`), and file formats (`quantarium-*-v1`) follow.
- Everything written under the names used before 1.0 still works (`quantarium/legacy.py`, `tests/test_legacy.py`): an older hidden workspace folder is renamed to `.quantarium` the first time a workspace opens, and saved paths that name it resolve to the new folder; the older workspace file, environment variables, agent tokens, keychain entries, and file-format names are read; an alias package keeps older imports, the older command, and saved entrypoints working. Saved records are read in their current form but never rewritten, so their fingerprints and evidence reports still verify. Agent apps never inherit variables under either name, and both token prefixes count as secrets in logs.
- **Security fix:** agents could start a user's decider through a config that spelled its entrypoint with a JSON escape (or, after the rename, under the package's earlier name), because the check searched the config's text. It now checks the decoded JSON for the decider class under any name; `tests/security/test_decider_misuse.py` fails without the fix.
- Format checks treat a non-string `format` as "not this format" instead of raising, which the data-folder fuzz test found when the checks were rewritten.

## v0.9.5 — 2026-10-26

v0.9 in the app, and the end of the v0.9 series.

- The Workspace page lists research data folders and trades files under `data/`: **Import as a scenario** and **Make a decision file**, with the import's warnings and the conversion's limits shown in place.
- Scenario cards and pages show the survivorship declaration; imported scenarios chart symbols that list and delist (the scenario route returns `null` on days a symbol does not trade, where it used to fail).
- A Complex cell's Portfolio rules take an **Index**.
- API: `data-check` and `trades-convert` commands and a `data-import` job, all user only; agents can read the resulting scenarios but cannot bring outside data in (`tests/security/test_outside_data.py`). `AGENTS.md` gains an invariant for this.
- Handbook chapter 15, "Your own data and trades".

## v0.9.4 — 2026-10-26

Futu OpenAPI market data, through a bridge.

- `tools/futu_bridge/bridge.py` runs in its own environment with `-I`, asks the user's local Futu OpenD gateway for daily candles (forward-adjusted by default), and writes a Static price file, or appends new sessions for a `csv` Feed. Quantarium never imports Futu's package and gains no dependency; the bridge needs no keys, since OpenD holds the login.
- It connects only to loopback addresses and uses only Futu's quote context; a test checks that its source names no trading API. It checks bars itself, and Quantarium checks the file again.
- Tested with fake candles; no Futu account was used. Setup, including a dry-run audit of `futu-api`'s dependencies before installing a pinned release, is in [docs/futu-bridge.md](docs/futu-bridge.md).

## v0.9.3 — 2026-10-26

Your own track record.

- `quantarium decisions from-trades` turns a generic trades CSV (`date,symbol,side,shares,price`) into a decision file: each fill becomes a market order decided at the previous close, on the NYSE calendar or a price file's sessions. Same-day trades are netted; round trips, shares sold before they were bought (with suggested starting holdings), and what a trades file cannot show are reported.
- `quantarium decisions gap` compares a replay's fills with the real ones: per trade, the mean gap in basis points weighted by value, and the total in dollars.
- The result is a finished decision file, so the Lab caps its verdict at `unknown`, as before.
- Trades files are untrusted input: bounded size, rows, and calendar span; seeded fuzz tests in `tests/security/test_trades.py`.

## v0.9.2 — 2026-10-26

Survivorship and selection in the Lab.

- The Lab pins each imported scenario's survivorship declaration. Verdict rows show it (`survivorship`: `survivorship_free`, `survivors_only`, `unknown`, or `not_declared` for other real-price scenarios; none for synthetic markets), and so do evidence reports and the Lab page.
- A verdict on real-price data not declared survivorship-free carries a caveat naming those scenarios. A candidate that trades a fixed list of symbols on real-price data carries a selection caveat, unless it holds an index's members as of each date (`index`). Neither changes the verdict's status; both say what could flatter it.
- Lab controls of an index candidate hold the same members as of each date, so the comparison stays fair.
- Suites say "declared free of survivorship bias, not verified" for such scenarios instead of the generic historical caveat.
- Cells in the library keep `index`; registrations without imported data keep their fingerprints.

## v0.9.1 — 2026-10-26

Changing universes ([ADR-0039](docs/adr/0039-changing-universes.md)).

- Static runs, suites, and the Lab replay symbols that list late, delist, and move in and out of an index. Imported research scenarios use this automatically; a Static config opts in with `changing_universe`, `delistings_path`, or `membership_path`. Without it the engine takes exactly its old path.
- A holding in a delisted symbol is paid out at the first session without a bar, at the delisting file's price or the last close, and recorded as a `delisted` event; its resting orders are cancelled.
- A cell with `"index": "NAME"` holds only that index's members as of each close. Strategies see only the symbols they may hold in `context.bars` and `context.history`, so existing strategies need no change. Weight on anything else stays in cash (`outside_universe` in the signal); buy orders outside it are rejected.
- Sessions come from the whole price file, so a cell whose stocks all delist still reaches the session that pays them out. A first version took the calendar from the cell's own symbols only; the new tests caught that it left a bankrupt stock valued at its last close.
- On a fixed universe, the changing-universe path replays every example scenario and strategy event for event as before (`tests/test_universe.py`), so the v0.8 cross-check still covers it. backtrader has no model of delistings or index membership, so those rules are tested on the invented example index.
- Feed Batch and Live Feed refuse index cells; the Lab's disguised replay skips imported data with a reason.

## v0.9.0 — 2026-10-26

Research data in Quantarium's own format.

- `quantarium-data-v1` ([docs/data-format.md](docs/data-format.md)): a folder with `source.json` (supplier, license note, and the user's survivorship declaration: `survivorship_free`, `survivors_only`, or `unknown`), `prices.csv` in which symbols may list late or stop early (but not skip a session in between), and optional `delistings.csv` and `membership.csv`.
- `quantarium data check` validates a folder and summarizes it, with warnings for prices that stop without a delisting, index members without prices, and data not declared survivorship-free. `quantarium data import` makes a historical scenario that copies the files with their hashes and records the source and declaration; the Lab counts the same prices as the same data, however they were imported.
- Every file is untrusted: bounded sizes and counts, strict JSON without duplicate keys or non-finite numbers, and seeded fuzz tests in `tests/security/test_user_data.py`.
- `examples/data/fictional_index`: an invented five-member index with a late listing, a bankruptcy, a cash takeover, and members leaving and rejoining. Quantarium still ships no market data.
- Replaying symbols that come and go is v0.9.1; until then such a scenario is refused by the engine with "Missing daily bars".
- The v0.9 plan ([docs/plans/v0.9.x-data.md](docs/plans/v0.9.x-data.md)) records the maintainer's decisions: brokerage market data only in v0.9 (paper trading moves to the post-v1.0 trading plan), Futu through a bridge in its own environment, Quantarium's own data format first, and a generic trades CSV for the user's own history.

## v0.8.3 — 2026-10-26

Faster and leaner replays.

- Static runs no longer make SQLite wait for the disk after every session. Each session is marked with a savepoint, and the journal commits at most once a second and at every cell's end, so a failing day still rolls back to exactly the day before. A test runs the same failing replay with commits every day and with none, and requires identical events.
- The price loader reads rows without building a dictionary for each, and bars share one string per symbol and one date per day. Error messages and line numbers are unchanged.
- Measured with `benchmarks/static_scale.py`, before and after: one year of 500 symbols, 1.58 → 0.74 seconds; ten years of 100 symbols rebalanced daily, 8.55 → 2.24 seconds; ten years of 500 symbols, 16.3 → 7.4 seconds, with peak memory 521 → 425 MiB.
- The probe now times replays without `tracemalloc`, which had inflated its timings about fourfold, and reports the process's peak memory; `--trace-memory` restores the traced figure. Feed restarts were already quick (0.15 seconds for 8 cells over 80 sessions) and are unchanged.

## v0.8.2 — 2026-10-26

Costs beyond a flat rate.

- Three optional costs for Static runs, suites, Feed experiments, and the Lab: `commission_per_share`, `commission_minimum` (a filled order pays the per-order and per-share commissions, but at least this), and `slippage_range_fraction` (extra slippage of this share of the previous session's high-to-low range, over its close, so volatile stocks cost more to trade). All default to zero, which reproduces earlier results exactly.
- Buys are capped so that shares and the whole commission fit the cash. The same rules apply to the daily rebalance, resting stop and limit orders, and starting holdings (which have no previous session, so no range slippage).
- Set in configs, the run dialog's Costs section, the Lab's registration form, the API, and the MCP tools. Lab registrations pin only costs that are set, so experiments registered before this keep their fingerprints; verdicts and evidence reports show them. Live Feed refuses them for now.
- The cross-check adds 12 cases with a per-share commission and a minimum: **all 68 cases match**. backtrader has no model of range slippage, so that is covered by unit tests (`tests/test_costs.py`).

## v0.8.1 — 2026-10-26

The cross-check, widened.

- 24 new cases, on all four example scenarios: deciding every fifth session, a volume cap that binds (464 of 481 orders cut short in one case), starting holdings bought at the first open, and resting orders: a trailing sell stop (`StopGuard`) and buy and sell limits (`DipBuyer`), run in backtrader with its own stop and limit orders.
- **All 56 cases match**: 9,543 fills identical in date, symbol, side, shares, and price, and daily equity within 2×10⁻¹⁵. No difference in Quantarium's engine was found. The one difference on the way was in the runner's setup (backtrader calls `prenext_open`, not `next_open`, at the first bar), now fixed.
- What the cross-check does not settle, and why, is recorded in the plan: corporate actions (backtrader has no model of them), position and cash limits (Quantarium stops a run that breaks them rather than trading differently), a stop and a limit touched on the same day (a daily bar cannot show which came first), and Live Feed's minute bars.

## v0.8.0 — 2026-10-26

The engine cross-check ([plan](docs/plans/v0.8.x-engine.md), [ADR-0038](docs/adr/0038-backtrader-as-reference-engine.md)).

- **backtrader 1.9.78.123 is the reference engine**, replacing zipline-reloaded in the roadmap: zipline's storage library has Windows wheels only up to Python 3.12, and installing it otherwise meant compiling C code or accepting dependencies younger than seven days. backtrader is one pure-Python wheel without dependencies. It lives in its own environment outside the repository; Quantarium never imports it.
- `tools/crosscheck/backtrader_runner.py` replays a case in backtrader with Quantarium's assumptions: decide at the close; at the next open, size to target weights from equity at the open, sell before buying, slip the open price, charge a flat commission per order, use whole shares, and cap buys by cash. `tools/crosscheck/run.py` runs the same case on Quantarium's engine, starts the runner as a separate process with `-I` and only basic system variables, and compares every fill and the daily equity.
- **Result: all 32 cases match** (four example strategies on four example scenarios, with and without slippage and commission): 5,351 fills identical in date, symbol, side, and shares, fill prices equal, and daily equity within 2×10⁻¹⁵. A negative control (0.5 bp more slippage in the reference only) is detected.
- Tests: `tests/test_crosscheck.py` covers the comparison and Quantarium's side; with `QUANTARIUM_REFERENCE_PYTHON` set it also runs two cases against backtrader.

## v0.7.3 — 2026-10-26

The app for v0.7. This completes the v0.7.x series.

- **Lab:** a finished experiment shows its trial-aware statistics (Sharpe ratio, probabilistic and deflated Sharpe ratios, attempts, and the probability of backtest overfitting or why it is not measured), and **Download evidence report** saves its self-contained page.
- **Model access** fills prices from the dated price table and says the date checked, or asks for prices when the table does not list the model.
- **Agents:** each model API connection has a **Daily cost limit** field.
- **Handbook:** the Lab chapter covers attempts and luck, and evidence reports, with two new help topics and feature rows.

## v0.7.2 — 2026-10-26

Model costs.

- **A dated price table** (`quantarium/assets/agents/prices.json`), checked on 2026-10-26 against Anthropic's and OpenAI's pricing pages, lists dollars per million input and output tokens for their current models; Ollama and LM Studio are free. `GET agents` includes it.
- **Grants take their prices from the table** when none are given. A model the table does not list now needs its prices entered: before, missing prices silently counted as zero, which left the cost budget with nothing to count.
- **A daily cost limit per model API connection** (`agent-connection-daily-limit`, user only), counted in UTC days across every cell's tape. Once reached, the gateway refuses further calls until the next day.
- Tests: `tests/test_prices.py`.

## v0.7.1 — 2026-10-26

Evidence reports.

- `quantarium lab report <experiment>` writes one self-contained HTML page per Lab experiment: the verdict and every caveat, the candidate against its controls, the trial-aware statistics, everything frozen at registration (cell version, scripts, decision files, inboxes, deciders, risk card, and scenario prices, by SHA-256), costs, the pass rule, the Quantarium version, and the limits of what it shows. It makes no network requests and runs no code; every value is rendered as text, and a Content-Security-Policy forbids anything else.
- The page embeds the record as JSON with the SHA-256 of that exact text. `quantarium lab verify-report <file>` checks the page against itself, and with `--workspace` also against the Lab ledger and the pinned files, listing any that changed since.
- `GET lab-report/{id}` returns the page for the app (browser only).
- Tests: `tests/test_evidence.py`, including a hostile experiment name and a forged number.

## v0.7.0 — 2026-10-26

Trial-aware statistics in the Lab ([plan](docs/plans/v0.7.x-evidence.md)).

- **Every Lab result reports**, per scenario, the candidate's annualized Sharpe ratio, its **probabilistic Sharpe ratio** (the chance its true Sharpe ratio is above zero, from the number of sessions and the skewness and kurtosis of its daily returns), and its **deflated Sharpe ratio**, measured against the Sharpe ratio the best of all attempts at the idea would reach by chance. Every registration of the idea counts as an attempt, including failed and stale ones; the spread of Sharpe ratios comes from the completed attempts, or from the candidate's own sampling error when there is only one.
- **The deflated Sharpe ratio gates the verdict.** A candidate that beats its controls is **supported** only if its deflated Sharpe ratio is at least 0.95 in every scenario; otherwise the verdict is **inconclusive**, with the reason.
- **Probability of backtest overfitting**, by combinatorially symmetric cross-validation, once an idea has four or more completed attempts on shared data. A value of 50% or more adds a caveat.
- `quantarium/trials.py`, standard library only (`statistics.NormalDist`). Tests in `tests/test_trials.py` check known values, the gate, and a Lab run.

## v0.6.5 — 2026-10-25

The referee, complete: examples, a guide, the app, and the handbook. This completes the v0.6.x series.

- **In the app:** under Outside decisions on a cell's Configure tab, **Open an inbox** (for the cell's symbols) and **Register a decider** (program, arguments, environment variables it may receive, and training cutoff), with a help button for the new chapter. Decider recordings appear among decision files.
- **Handbook chapter 14**, "The referee: judging outside decisions", with four new help topics and feature rows; a test keeps them covered.
- **Examples:** `examples/decisions/steady_bull_weekly.csv`, a weekly moving-average decision file for the steady-bull scenario, and `examples/deciders/moving_average.py`, a complete decider in the standard library. A test runs both.
- **Guide:** `docs/referee.md`, covering the three ways in and what each can prove, the formats, exporting from other systems, forward inboxes, and the decider protocol.

## v0.6.4 — 2026-10-25

Live deciders: outside programs that Quantarium runs session by session.

- **Deciders** (`quantarium/deciders.py`). The user registers a command line, the environment variables it may receive, and optionally its model's training cutoff (`quantarium decisions decider-create`, or the `decider-create` command). Quantarium starts it in an empty folder, with only basic system variables plus the named ones, and speaks JSON lines: one message per session with data up to that session, one decision back. Replies are checked like decision files, limited to 1 MB, and to 120 seconds per session.
- **Decider cells** (`{"decider": name}`), chosen on the Configure tab, run in Static, suites, and the Lab. Every run records the answers as a tape in `decisions/recorded/`; tapes read as decision files, so a run can be rescored without the program.
- **The Lab** runs a decider live, pins its command, variables, and cutoff, and judges it like a model-driven cell: synthetic and disguised markets are clean, and real history counts only after the recorded cutoff. A changed decider makes a registration stale.
- **Agents cannot register, read, choose, or run deciders**: a decider is the user's program and may spend the user's money. Agents are refused when saving a cell with a decider, deploying one, or running a config or Feed experiment that names one.
- `AGENTS.md` lists deciders and forward inboxes among the security invariants.
- Tests: `tests/test_deciders.py` with `tests/fake_decider.py`, and `tests/security/test_decider_misuse.py`.

## v0.6.3 — 2026-10-25

The forward referee: decisions submitted before their sessions can earn a full verdict.

- **Forward inboxes** (`quantarium/inbox.py`). An inbox is an append-only file under `.quantarium/inboxes/` for decisions about a declared set of symbols. Quantarium stamps each submission's arrival by its own clock. A decision for a session's close counts only if it arrived before the **next session opened** (09:30 New York, by the NYSE calendar), when no one could know that session's prices. A later on-time decision for the same date replaces the earlier one; late ones are kept on record and left out. Nothing is ever rewritten.
- Submit from the command line (`quantarium decisions inbox-create`, `quantarium decisions submit`), the API (`decision-inbox-create`, `decision-submit`, `GET decision-inboxes`), or MCP (`create_decision_inbox`, `submit_decision`, `list_decision_inboxes`). Agents need the simulate tier, and a new budget limits them to 500 inbox actions a day by default.
- **Inbox cells.** A cell can follow an inbox (`{"inbox": name}`), chosen on the Configure tab alongside decision files. Feed Batch reads the inbox afresh at each advance.
- **Full Lab verdicts.** Registration freezes the inbox (its length and SHA-256); later submissions are not part of the experiment, and a rewritten inbox makes it stale. The verdict is not capped, but it is only allowed on scenarios of real prices: forward decisions were made in the real market, and on a synthetic market the "future" prices already exist in the workspace. Caveats name late decisions and scenarios that began before the first on-time decision.
- Tests: `tests/test_inbox.py`, `tests/security/test_inbox_misuse.py`; the request fuzzer covers the new commands.

## v0.6.2 — 2026-10-25

The Lab for decision files.

- The Lab registers and runs cells that replay a decision file, against the usual buy-and-hold and equal-weight controls. Registration pins the file's SHA-256 and date range; a file changed afterwards makes the experiment stale.
- Such a result is marked `decisions: after_the_fact` and its verdict is capped at **unknown**: Quantarium cannot tell whether a finished file's decisions were made at the time or afterwards, with hindsight. Costs, controls, and drawdown are still reported, with the reason.
- A decision file cannot be replayed on a disguised market, since its dates are fixed. A scenario outside the file's dates gets a caveat that the candidate only held there.
- Tests in `tests/test_referee.py`.

## v0.6.1 — 2026-10-25

Rescoring: a cell can replay a decision file on Quantarium's engine.

- **Decision cells.** On a cell's Configure tab, choose **A decision file** instead of scripts, and pick a file from the workspace's new `decisions/` folder. At each close the cell takes that date's decision; a date without one keeps the holdings. Fills, costs, and portfolio rules are Quantarium's, as for any cell, and it runs in Static and scenario suites.
- The built-in strategy behind it, `quantarium.referee:ReplayDecisions`, pins the file's SHA-256 when a run is set up; a file changed after that stops the run instead of replaying other decisions.
- A decision cell has exactly one decision file and no scripts, allocator, model access, or risk card, rebalances every session, and holds every symbol its file names. Problems are listed before anything runs.
- The workspace overview lists decision files with their summaries, or what is wrong with each.
- The Lab does not judge decision cells yet; v0.6.2 adds that, with results capped at `unknown` because a finished file may use hindsight.
- Agents' path rules now cover decision files named in a cell.
- Tests: `tests/test_referee.py`, and agents' decision paths in `tests/security/test_decision_files.py`.

## v0.6.0 — 2026-10-25

The first step of the referee ([plan](docs/plans/v0.6.x-referee.md), [ADR-0037](docs/adr/0037-referee-not-competitor.md)): a file format for trading decisions made outside Quantarium.

- **Decision files** (`quantarium/decisions.py`). JSON is the exact format (`quantarium-decisions-v1`): the source system, its symbols, and dated decisions, each with target weights or `null` to hold, optional orders, and a note. CSV with the columns `date,symbol,weight` carries weights only. A decision is made at the close of its date and fills at the next open. The reader is strict: weights from 0 to 1 totalling at most 1, listed symbols, unique ISO dates, valid orders, plain-text notes, no `NaN` or infinities, and limits on size (10 MB), decisions (50,000), and symbols (500).
- `quantarium decisions check <file>` validates a file and prints its summary and SHA-256.
- Request bodies and decision files share one strict JSON reader (`quantarium/strictjson.py`).
- Tests: `tests/test_decisions.py`, and seeded mutation fuzzing of JSON and CSV decision files in `tests/security/test_decision_files.py`.

## v0.5.5 — 2026-10-24

The v0.5.x security hardening, released together as v0.5.5: misuse tests of Quantarium's own code, and fixes for the eighteen findings they produced (two high, six medium). The sections below record each step.

- The README now describes Quantarium as it is: a simulator, a lab for evidence, and a safe harness for AI agents. It adds what makes Quantarium different, a five-minute tour, a Security section, and current next steps, and corrects stale claims about sandboxing, risk cards, and the handbook.
- Kandodaki's ML research (PR #2) was closed unmerged and kept as research reference; its sandbox `ctypes` cleanup was ported (see v0.5.2).

### v0.5.5 — Report

- `docs/security-review-v0.5.md`: the threat model, the rules the misuse tests follow, eighteen findings and their fixes (two high, six medium), what was checked and held, accepted risks, and what remains untested.
- The code execution tests also check that agents cannot write alternate data streams, short 8.3 names, or device names on Windows.

### v0.5.4 — Models and agent apps

- **Secrets in agent apps' environments (medium).** Agent apps received every environment variable except a short list of model and broker key names, so `GITHUB_TOKEN`, `AWS_SECRET_ACCESS_KEY`, and similar secrets reached them. Quantarium now also removes variables whose names contain `SECRET`, `TOKEN`, `PASSWORD`, `CREDENTIAL`, `API_KEY`, `ACCESS_KEY`, or `PRIVATE_KEY`, end in `_KEY`, `_SECRET`, or `_PAT`, or start with a known provider's prefix. Agent apps must sign in through their own login, as before.
- **Disguised tools (medium).** A tool call counted as one of Quantarium's when its title merely contained a Quantarium tool's name, so an agent app that reported a shell command such as `curl … | sh # quantarium__list_cells` without marking it as a command got permission for it. The whole title must now name the tool.
- **Budget and odd usage (medium).** The `ask` gateway read token usage before counting a call, so a model API that reported usage as text or `NaN` was called again and again without using up the call budget. The call is now always counted, and output the API does not report plainly counts as the most the model may write.
- **Loopback names (low).** Plain `http://` endpoints now accept only `localhost` and loopback addresses. Other `*.localhost` names may be resolved by a DNS server, and a key sent over plain HTTP would follow the answer.
- **Floods and malformed messages from agent apps (low).** A turn stops after 200,000 characters of text or 200 tool calls. Updates and requests of the wrong shape are ignored or answered with an error, and odd usage reports count as zero, instead of ending the turn with an exception.

New tests in `tests/security/`:

- `test_models_and_apps.py` with `hostile_acp.py`, a misbehaving fake agent app. Endpoints that could carry a key elsewhere are refused. Crafted model answers never pass the schema, and odd usage never lets a model exceed the call budget. Disguised tools are refused, floods are cut off, and malformed messages are answered without ending Quantarium's side. Agent apps get no canary secrets and an empty folder.
- A static check keeps every link built from data inside the app.

### v0.5.3 — Files and data

- **Reused data under a new name (medium).** The Lab recognized reused data by scenario family, which for a generated or imported scenario is its manifest hash. Regenerating the same scenario under another name, or copying its folder and renaming it, produced identical prices with a new family, so an idea could be tested again on used data and still earn a verdict. Reuse is now also matched on the exact prices, and an experiment whose scenarios share prices gets the "not independent evidence" caveat.
- **Damaged tapes (low).** A tape entry with a `NaN`, negative, or non-numeric token count or cost could switch the gateway's cost budget off (`NaN` passes every comparison) or break the Supercells view. The gateway now refuses such a tape, and the summary leaves damaged entries out.
- Price files with short rows, vendor files with infinite values, and configs or suites whose entries have the wrong type now fail with an ordinary error (a 400 at the API) instead of an `AttributeError` or `OverflowError` (a 500).

New tests in `tests/security/`:

- `test_files.py`: seeded mutation fuzzing of price, vendor, and action CSVs, scenario manifests, Static configs, suites, and risk cards. Parsers may raise only ordinary errors, and accepted bars must have finite, positive prices. Damaged cells, scenarios, configs, scripts, and databases never make a read return a 5xx. Damaged agent settings and provenance fail closed. Damaged tapes never switch a budget off.
- `test_lab_integrity.py`: the same prices under a new name, or in a copied folder, count as used data.

### v0.5.2 — Code execution

Fixes for three ways unreviewed agent code could run inside Quantarium's own process, and so outside the sandbox:

- **Path spellings (high).** Provenance recorded an agent-written script under the path exactly as the agent spelled it, but checked it under the file's canonical path. A script written as `strategies/agent/./x.py`, `strategies/agent//x.py`, or with different capitals on Windows was then treated as the user's own and ran in-process, with only the default tiers. Writes are now recorded under the canonical key, and every comparison normalizes both sides, so older records match too.
- **Imports from reviewed scripts (high).** The loader checked only the script it was asked to run. A reviewed script that imported an unreviewed agent-written module ran that module in-process. An import guard now refuses unreviewed agent-written files for every import in the process, however they are reached.
- **The import path (medium).** Loading a strategy left its folder at the front of the server's `sys.path`, so a later import anywhere in the server could resolve to an agent's file of the same name. The folder is now on the path only while the strategy loads.
- The sandbox child drops `ctypes` from its module cache before its audit hook goes on, so a strategy's `import ctypes` is refused by the hook even when numpy loaded it first; the code policy already refused it. Adapted from Kandodaki's ML branch (PR #2), which is closed unmerged and kept as research reference.
- `script-write` and `script-check` no longer apply path rules to a script's source code, which refused scripts whose text happened to contain `../` or a dotted folder name.

New tests in `tests/security/test_code_execution.py`:

- Eight spellings of agent script paths, each run as a cell, never load in-process.
- A reviewed script cannot import an unreviewed one, and agent files are not importable by the server.
- Agents cannot write `.pth`, `.pyc`, `.pyw`, package, `sitecustomize`, hidden, or outside files.
- Unreviewed scripts that open a canary file, import `os` or `socket`, or reach for dunders are refused by the sandbox.
- Model cards outside the workspace or in hidden folders are refused. Agent-written allocators follow the same rules as strategies.

### v0.5.1 — Requests and permissions

Fixes found by the new misuse tests:

- The server now drops a client that stalls for 30 seconds while sending a request or reading a reply. Before, a client that stopped mid-request held a server thread indefinitely.
- Request bodies nested more than 64 levels deep are refused with a 400. A deeply nested body used to exhaust the interpreter's recursion limit and return a 500.
- Request bodies may no longer contain `NaN`, `Infinity`, or numbers too large to be finite. Python's JSON reader accepted them, and `NaN` passes every bound check unnoticed.
- A cell name must be text of at most 200 characters without control characters. A name that was a number or an object crashed `cell-create` and `cell-save` with a 500.
- `cell-save` refuses a `spec` that is not an object with a 400 instead of a 500.
- Static and suite deployments check that trading costs are finite and nonnegative before writing a config, instead of failing later inside the job.
- The MCP server answers a malformed `tools/call` (params or a tool name of the wrong type) with an error, and survives any message that fails, including one nested too deeply to parse. Before, such a message ended the MCP session.

New tests in `tests/security/`:

- Seeded fuzzing of every agent operation and prefix route, and of every browser read, command, and job except `chat-send`. No request may return a 5xx, leak a canary, reach the network, or change anything outside the workspace.
- Every path parameter refuses hidden and outside paths. Fourteen spellings of a holdout scenario's path are refused across reads, jobs, and Lab proposals, and the holdout never reaches an agent.
- An observer-only connection cannot change anything, by any route spelling. Browser-only reads stay closed to agents, and wrong or revoked tokens are refused.
- Malformed and slow HTTP requests, foreign hosts and origins, and malformed MCP messages.
- The harness now also blocks every non-loopback connection and name lookup, and gives each test an in-memory keychain.

### v0.5.0 — The security harness

- A new security suite in `tests/security/`, run with the normal test suite. Its misuse tests use Quantarium the way a careless or hostile agent or file might, against a disposable workspace on loopback, and check that Quantarium refuses safely.
- Canaries: each test plants fake API keys, broker keys, and tokens in the environment, and fake SSH, cloud, agent-app, and browser files in a fake home folder. Every test ends by checking that no canary reached a response or any file in the workspace.
- A folder guard checks that nothing outside the test workspace changed: the fake home, a sentinel folder beside the workspace, and Quantarium's own source.
- `MisuseCase` starts a test server with an agent connection that holds every tier. A seeded fuzzer produces hostile parameter values, so any failure can be replayed from its seed.
- Static checks allow `exec`, `eval`, dynamic imports, `pickle`, `marshal`, shell commands, and YAML loading only where a written reason says so (the trusted strategy loader and the sandbox child). Pages may not render text as markup or code.

## v0.4.10 — 2026-10-24

- `quantarium serve --fresh` serves a new, empty workspace (a folder under `~/quantarium-workspaces/`, or `--fresh-root`), so the dashboard and the tutorial start from a clean slate. Serving Quantarium's own source folder now prints a note that it doubles as the workspace.

## v0.4.9 — 2026-10-24

The tutorial, up to date with v0.4. This completes the v0.4.x series.

- New handbook chapter **13 · Supercells: cells driven by models**: risk cards, model access with `context.ask`, the Answers tab and the tape, allocators, and reading a supercell's evidence.
- The Lab chapter now covers model memory and training cutoffs, what each part adds, the disguised replay, the Supercells view, and why a tie with a control is not support.
- The Complex portfolios chapter covers starting holdings, resting orders, and allocators. The agents chapter covers recording a model's training cutoff.
- Seven new help topics: supercells, model access, answers and the tape, allocators, model memory, disguised replay, and holdings and orders. The feature catalog lists every v0.4 feature and where it is available.
- Help buttons on the Lab's Supercells view, the Allocation panel, Model access, and the Answers tab open the right chapter.
- A new test keeps the handbook consistent: every help button's topic belongs to a chapter, every chapter topic has an entry, and every feature row names a real chapter.

## v0.4.8 — 2026-10-24

Model access from the app.

- **Grant model access in the app.** A cell's Configure → Advanced section has a form for giving one of its scripts `context.ask` access: the script, the model connection (with its training cutoff), record or replay mode, and every budget. The server pins the script's exact bytes and keeps the cell's existing tape, so its spending history stays. Agents cannot grant access (`cell-ask-grant` is app only).
- A model on this computer costs nothing, so token prices in a grant may now be zero. The cost budget must still be positive.
- **The Answers tab** shows a cell's tape, newest first: each session's answer, or the reason a call failed, with tokens and estimated cost. Its summary shows what the budget has left, counting failed calls.
- **The Timeline** shows what the model answered each session, and whether the answer came from the tape.
- **Lab progress.** While an experiment runs, the Lab page shows its stage (the candidate with its controls and variants, then the disguised replay), the scenario, and how many model calls the candidate has made so far.

## v0.4.7 — 2026-10-24

Supercells in the Lab, the first of three frontend versions that finish v0.4 ([plan](docs/plans/v0.4.x-lab-and-supercells.md), section 13).

- The Lab page has a **Supercells** view beside its experiments. Each model-driven cell gets a card with:
  - its parts: risk card, the model it asks, or its allocator;
  - for a cell that asks a model: the model's training cutoff, the grant's mode, the tape's answers, failed calls, tokens, and estimated cost against its budget;
  - the evidence of its latest Lab experiment: the verdict, model memory, what each part adds, and the disguised replay's gap;
  - a warning when that evidence is for an earlier version.
- **Test in the Lab** opens a registration with the cell already chosen; **Open experiment** jumps to its latest verdict.
- Home marks every model-driven cell as a **Supercell**, not only those with a risk card, and shows its latest Lab status.
- `GET supercells` lists them for the app. Agents cannot read it, because it reads the cells' answer tapes.

## v0.4.6 — 2026-10-24

Composite and regime supercells ([ADR-0036](docs/adr/0036-composite-and-regime-supercells.md)). This completes the v0.4.x plan.

- A Complex cell can use an **allocator** that decides each script's share of the money at every close, instead of fixed shares. A regime supercell gives everything to one script, and a composite one splits it.
- Built in: **Trend regime**, which switches between two scripts by whether a symbol closes above its moving average, and **Fixed allocation**. Your own allocator is a class with `allocate(context, sleeves)` in a script beside the cell's other scripts.
- The cell page's new **Allocation** panel chooses fixed shares, the trend regime, or an allocator from your scripts.
- The Lab also runs a cell with an allocator on fixed shares, and reports what the allocator added.
- Allocators must be reviewed. One an agent wrote is refused, not sandboxed. Agents' path rules and the Lab's and proposals' script pins cover allocator scripts.
- An allocator may ask a language model, under an `ask` grant for its script.
- Feed checkpoints keep an allocator's state. Live Feed refuses allocators for now.

## v0.4.5 — 2026-10-24

LLM and hybrid supercells in the Lab ([ADR-0035](docs/adr/0035-llm-and-hybrid-supercells.md)).

- **Training cutoffs.** A model API connection can record its model's training cutoff, as the provider discloses it, on the Agents page or with `agent-connection-cutoff`. A Lab experiment with a cell that asks a model can now earn a verdict on real history, but only for scenarios that begin after that cutoff. Each scenario is labelled clean, contaminated, or unknown, and the verdict is `unknown` unless all are clean.
- **Lineage.** The Lab records whether each scenario comes from real history, following transformations back to their source. Changing a real scenario does not make it synthetic.
- **What each part adds.** For a hybrid, the Lab also runs the cell without its language model, holding its last target whenever it would have asked, and without its risk card. It reports each part's contribution.
- **Disguised replay** (optional per experiment). The Lab reruns the cell on copies of its real-history scenarios with invented symbols, future NYSE dates, and rescaled prices, and reports the return gap. It is a diagnostic and does not change the verdict.
- The Lab page shows model memory, each part's contribution, and the disguised replay. The Agents page shows and edits each model API connection's training cutoff.
- **Fixed in the `ask` gateway, found with a real model:**
  - when a reasoning model used its whole output budget thinking, the error now says so instead of "not valid JSON";
  - calls that yielded no valid answer are now recorded and counted against the budgets; before, their tokens and cost went uncounted.
- Verified end to end with qwen3:4b through local Ollama. A cell that asked the model every session was not supported against buy-and-hold (+5.2% against +10.2%). The model added 5.2 points over the cell without it, and the disguised replay differed by 0.02 points.

## v0.4.4 — 2026-10-23

The `ask` gateway and the answer tape ([ADR-0034](docs/adr/0034-ask-gateway-and-tape.md)), from a ChatGPT draft reviewed and completed by Claude.

- A strategy can ask a language model for a decision with `context.ask(prompt, schema)`. Quantarium makes the call through one of the user's model API connections, with no tools, and returns only an answer that matches the schema. Sandboxed agent code still has no network access or keys; its requests cross the sandbox's pipe.
- The user grants this per cell, to the exact bytes of one reviewed script, with budgets for calls, tokens, and estimated cost, and a decision rhythm. Agents cannot add, change, or copy a grant, and an agent's script must be reviewed first. Live Feed refuses cells with a grant.
- Every answer goes on the cell's tape in `.quantarium/tapes/` and in the journal. A replay reads the tape instead of calling the model, so the same backtest gives the same result at no cost.
- The Lab judges a cell that asks a model only on synthetic scenarios. On real or transformed history its verdict is `unknown`, because the model may remember what happened.
- The cell page's Advanced section shows a cell's model access and can remove it.
- Model API requests without tools no longer send an empty tool list, which OpenAI's API refuses.
- **Fixed the app's pages failing to load since v0.4.1.** A missing parenthesis in the cell page's script (from v0.4.1) left the app blank, because the main script imports it. A new test checks that every script in the app has balanced brackets, strings, and template literals, so a mistake like this fails the test suite.
- Fixed in the draft: the decision rhythm carried over between runs, replay mode misreported missing answers, and thinking blocks or fenced answers were refused.
- Verified with a local model: qwen3:4b through Ollama answered a strategy within its schema, and the replay used the tape.

## v0.4.3 — 2026-10-23

Fixes from a review of v0.4.0 to v0.4.2.

- **A Lab verdict no longer calls a tie "supported".** The mean edge over the best control must now exceed the registered threshold, not merely equal it. A candidate that matches its best control in every scenario is `not_supported`, with a caveat saying it added nothing. Before this fix, a copy of the buy-and-hold control passed the default rule with an edge of exactly zero.
- A Lab verdict now shows why a candidate failed. Suite report rows carry each cell's error, and the verdict repeats it as a caveat. Before, the reason stayed in the journal and the Lab showed none.
- Agents can no longer save a cell whose model card or sleeve script points into a hidden folder or outside the workspace. Quantarium already refused to load such paths; now they are refused when saved too, like every other agent path.
- A Simple cell's strategy that returns a dict of weights instead of one number now gets a message saying so, instead of a Python type error.

## Security maintenance — 2026-10-23

- Bounded the remaining build, Windows time-zone-data, and assistant keyring dependencies. A repository policy test now rejects unapproved or unbounded packages and catches workflow actions without full commit SHA pins or pip installs without hashes.
- Kept the old expectations workflow on the unmerged ML branches for separate review; it remains absent from `main`.

## v0.4.2 — 2026-10-22

- Added data-only, content-addressed risk model cards and `quantarium ml train-risk`. Training runs in a separate process, uses labels that end no later than `--through`, and records its input digest and user-declared source. Optional `[ml]` dependencies accelerate fitting; small studies can use the standard-library fallback.
- A configured cell receives point-in-time daily drop-risk estimates in `context.risk_estimates` and its event journal. The model does not place orders. Home marks model-assisted cells, and Timeline shows estimates.
- Lab registrations pin model-card bytes and can use the card's final training-label date as a bound information boundary. Runs beginning on or before it receive an `unknown` verdict. The card cannot prove its source claim or undisclosed offline training ([ADR-0032](docs/adr/0032-data-only-risk-model-cards.md)).

## v0.4.1 — 2026-10-22

- Added the **Lab** page and local experiment ledger ([ADR-0031](docs/adr/0031-lab-pre-registration-and-evidence.md)). A registration freezes one candidate cell, scenario files, simulated costs, and a pass rule before running. Each experiment automatically runs buy-and-hold and equal-weight controls and records period, universe, costs, boundary status, attempts observed, and caveats.
- Reusing a scenario family for the same idea makes subsequent runs exploratory with an `unknown` verdict. Changed pinned inputs make a registration stale. Self-declared information boundaries also yield `unknown` until model-card verification exists.
- Agents can read the Lab ledger through MCP and propose registrations for user approval in the Inbox; holdouts remain excluded. The approval pins scenario data as well as cell and script versions.
- Cell configuration now exposes and preserves starting holdings and the declared information end date.

## v0.4.0 foundations — integrated in v0.4.1

- Added starting holdings with cost basis and next-session market, limit, and stop orders for one-strategy Static and Feed Batch cells. Order state survives Feed Batch checkpoints; splits adjust resting orders and cost basis. Existing weight-only cells keep their behavior.
- Added a self-declared `information_end` boundary to cells. Daily valuation events record whether a session is after it, and the Static journal reports its unverified source. It is groundwork for Lab evidence checks, not a verified verdict.
- Revised the v0.4.x plan after a ChatGPT-6 Sol review: evidence claims are scoped, unknown provenance remains unknown, disguised replay is diagnostic, and statistical metrics require their full inputs.

## v0.3.4 — 2026-10-21

- Added the **Google Gemini** model API preset, through Gemini's OpenAI-compatible endpoint (`https://generativelanguage.googleapis.com/v1beta/openai`, key in `GEMINI_API_KEY`, models listed by the endpoint). This completes the v0.3.x model connections ([plan](docs/plans/v0.3.x-model-connections.md)).
- Gemini CLI is not offered as an agent app. Google's terms forbid third-party access to the services behind Gemini CLI with a Google account, and Gemini CLI could fall back to that sign-in, because Quantarium passes agent apps no API keys.

## v0.3.3 — 2026-10-21

- Added the **xAI (Grok)** model API preset (`https://api.x.ai/v1`, key in `XAI_API_KEY`, models listed by the endpoint). Grok plans are not offered: xAI's terms for using them from other apps are unclear.
- GitHub Copilot, planned for this version, is skipped. GitHub Models, its API, was retired on July 30, 2026. Copilot CLI with a Copilot plan can still be connected as a Custom ACP agent, at the user's own risk.

## v0.3.2 — 2026-10-21

OpenCode, the first preset added on the v0.3.1 ports ([plan](docs/plans/v0.3.x-model-connections.md)).

- **OpenCode as an agent app** (experimental). The assistant runs your own `opencode acp`, with whatever OpenCode is set up to use: its free models, OpenCode credits, or the providers you connected in it. For Quantarium turns, OpenCode's own tools (shell, file reading and editing, web, subagents, and others) are switched off and denied through its inline settings, and every other tool must ask first, so Quantarium allows its own tools and refuses the rest.
- **OpenCode as a model API**, for pay-as-you-go credits at `https://opencode.ai/inference/openai/v1`. OpenCode's free models are refused outside OpenCode itself, so the preset card points to the agent app for those.
- Runtime presets can set the agent app's own settings for Quantarium turns through environment variables (`env` in `presets.json`). The preset test refuses Quantarium's, the broker's, and secret-looking variable names.
- An empty model reply now says so in the chat, instead of showing an empty message.
- Verified with a real local model: Ollama with `qwen3:4b` listed its models, passed Test connection, and answered through Quantarium's tools.

## v0.3.1 — 2026-10-21

The built-in assistant connects to models through two provider-neutral ports instead of per-vendor code ([plan](docs/plans/v0.3.x-model-connections.md)).

- **Model APIs** ([ADR-0021](docs/adr/0021-model-api-port.md)). A connection names a protocol (OpenAI Chat Completions or the Anthropic Messages API), an endpoint, and a model, so any compatible service works: presets for Anthropic, OpenAI, OpenRouter, Groq, Mistral, DeepSeek, Ollama, and LM Studio, or **Custom** for anything else.
  - Presets are data in `quantarium/assets/agents/presets.json`, checked by a test.
  - Keys go only to the connection's endpoint, over HTTPS except for servers on this computer, and never follow a redirect. The Agents page shows the host before a key goes somewhere a preset doesn't name. Local servers can run without a key.
  - **Test connection** checks that the model can call tools; **Models** lists the endpoint's models and switches between them.
  - v0.3.0 connections keep working and are read in the new form.
- **Agent apps** ([ADR-0022](docs/adr/0022-agent-runtime-port.md)). The assistant can run in an app you installed and signed in to yourself, through one interface with three profiles:
  - **Codex**, the v0.3.0 bridge, unchanged in behaviour;
  - **Claude Code** with your Claude plan, permanently experimental and not verified with real accounts: your unmodified `claude` with no built-in tools, only Quantarium's MCP server from a per-turn file, Quantarium's tools only, and no user settings. Quantarium never touches its sign-in. Creating the connection requires accepting that the decision, and any action Anthropic takes on your account, is yours;
  - **Any Agent Client Protocol agent**, experimental: Quantarium offers no file or terminal access, allows only its own tools when asked, stops the turn if another tool is reported, and cancels on Stop.
  - Every turn gets a one-turn token, an empty folder, and an environment without Quantarium, broker, or model API keys. Deleting a chat can delete the app's transcript too.
- The Agents page has one form for the three connection kinds (external agent, model API, agent app), with a card per preset that says who bills the usage, where conversations are kept, and the terms status.

## v0.3.0 — 2026-10-21

Quantarium becomes agent-compatible ([plan](docs/plans/v0.3.0-agent-interface.md)). An AI model can read the workspace, draft cells and scripts, run Static tests, and propose changes for the user to approve. It works through external MCP clients or through the new built-in assistant. Every agent feature is off until the user accepts a notice, and every limit is enforced by Quantarium's own code, not by the model.

- Added the switch for agent features ([ADR-0014](docs/adr/0014-agent-connections-and-permissions.md)). They are off by default in every workspace. Turning them on requires reading a notice and typing a confirmation, on the new **Agents** page or with `quantarium agents enable` in an interactive terminal; a changed notice turns them off until it is accepted again. `quantarium agents status` and `quantarium agents disable` complete the command.
- `GET jobs/{id}` accepts `wait` to return when the job finishes.
- Every API error now has a machine-readable `code`, and many have a `hint` that says what to do next (for example `cell_not_ready`, `cell_not_found`, `name_taken`, `feed_settings_changed`). Failed jobs carry them too. Codes are listed in the API reference.
- Job submissions accept an `idempotency_key`, so a retried request returns the first job instead of running twice.
- Added script endpoints: `GET scripts/{key}`, `script-write` with a base hash so a write never overwrites an edit saved in the meantime, and `script-check`, which parses without running. Writes stay inside the scripts folders and outside hidden folders. Agents write only under `strategies/agent/` unless a connection is allowed to edit the user's scripts.
- Scripts an agent writes are recorded by content hash. Until the user reads one on the Agents page and marks it reviewed, or edits it, it runs only in a sandbox during Static runs and suites, and Feed and Live refuse it ([ADR-0016](docs/adr/0016-running-agent-written-strategies.md)). The sandbox is a separate process with a code policy, a deny-by-default audit hook, no secrets in its environment, memory, CPU, and process limits, and a timeout on every call. `script-check` reports `sandbox_problems`. The script list marks versions awaiting review. Agent requests wait while the tutorial runs.
- Added proposals ([ADR-0017](docs/adr/0017-proposals-and-approvals.md)). Agents with the `propose` tier ask for a Feed forward test, an Advance, retiring a cell, or archiving and restoring cells. Only the user approves or rejects, in the app. A proposal pins the cells' settings and scripts' hashes it was made against and goes stale if they change. Approving a forward test reviews its scripts. The new **Inbox** page lists proposals with the agent's reasoning, the exact action, cells, and scripts, and a sidebar badge counts those waiting; Approve on a forward test unlocks after each agent-written script has been read. MCP gains `propose_feed_test`, `propose_advance`, `propose_archive`, `propose_restore`, `propose_retire`, and `list_proposals`.
- Added holdout scenarios and variant counting ([ADR-0018](docs/adr/0018-holdout-scenarios.md)). The user marks scenarios as held out from agents on the Scenario page. Agents cannot read them, run on them, or see results on them, recognized by content so copies stay hidden. An agent may propose one holdout test per cell version (`propose_holdout_test`), whose results only the user sees. Summaries and proposals report how many cell variants the agent has simulated, with a caveat from 20.
- Added the built-in assistant ([ADR-0019](docs/adr/0019-in-app-assistant-and-model-connections.md)). A chat panel docked beside every page talks to your own Claude or OpenAI model, through the official SDKs in the optional `quantarium[assistant]` extra.
  - It uses the same tools, checks, budgets, and audit log as MCP agents, and it cannot approve proposals.
  - Replies can show live cell cards, jobs, and Inbox links. Chats are kept in `.quantarium/chats/` with token counts.
  - Keys come from an environment variable or the system keychain and are never stored by Quantarium.
  - Anthropic connections default to Claude Opus 5.5, with effort high, prompt caching, and server-side refusal fallback.
- A security review before release ([report](docs/security-review-v0.3.0.md)) found and fixed seven issues:
  - agents could read other jobs' results, including holdout results;
  - some holdout routes were unguarded;
  - the sandbox code policy had gaps around introspection and numpy file reading;
  - an agent's copy of a user's script could make the original look unreviewed;
  - an unlocked `agents.json` could lose a revocation;
  - a negative Content-Length hung a request thread;
  - an unchecked Codex thread id reached Codex's command line.
- The assistant panel can be resized and widened.
- Added prompt-injection fixtures: hostile text is planted in a scenario, a cell name, and a script, and an agent with every permission tries everything it asks for. Every attempt is refused and nothing changes. MCP tool results now carry a reminder that workspace text is data, not instructions.
- Added handbook chapter 12, "Working with an AI agent", and help buttons on the Agents and Inbox pages.
- Added the Codex bridge ([ADR-0020](docs/adr/0020-subscription-bridges.md)): the assistant can run on a ChatGPT plan through the user's installed Codex CLI.
  - Codex reaches Quantarium only through `quantarium mcp`, with a token valid for one turn.
  - Its own tools are disabled, and it runs read-only in an empty folder without the user's Codex config or environment secrets.
  - If it starts a command anyway, Quantarium stops the turn.
  - Deleting a chat removes its temporary folder and, if you choose, Codex's own transcript of it.
- Agent requests cannot name paths in hidden folders such as `.quantarium/` or paths with `..`. They write scenarios only inside the scenario folders, need `manage` to replace one, and cannot choose suite names, which could replace an existing config.
- Added `quantarium mcp`, an MCP server over stdio for agents such as Claude Code, Claude Desktop, and the Codex CLI ([ADR-0015](docs/adr/0015-agent-interface-over-mcp.md)). It forwards to the workspace's `quantarium serve` with the connection token in `QUANTARIUM_TOKEN`, starting a background server if none runs, and offers tools to read the workspace and results, draft cells and scripts, generate and transform scenarios, run Static tests and suites, and wait for jobs. The tool catalog is in `docs/api.schema.json`. `quantarium serve` announces itself in `.quantarium/server.json`. With agent features off, `quantarium mcp` refuses to start.
- `GET analysis` and `GET suite-report` accept `summary=1` for a compact form with headline metrics, warnings (short history, few trades, one session carrying the result, early stops), and caveats (no costs, synthetic or single-scenario data). The analysis curve can be paged with `curve_offset` and `curve_limit`.
- Added agent connections. Each has a bearer token, shown once and stored only as a digest, and a set of permission tiers (`observe`, `draft`, `simulate`, `propose`, `manage`). Every agent request is checked against agent features being on, the token, and a single table of allowed operations; anything not listed is refused. Connections are created and revoked on the Agents page.
- Added an agent audit log (`.quantarium/agent-log.sqlite`) of every agent request and its outcome, with secrets removed, shown on the Agents page. Connections get budgets (jobs per hour, cells created per day), and **Pause all** stops every connection at once.

## v0.2.2 — 2026-10-21

- The tutorial is now a state of the workspace ([ADR-0013](docs/adr/0013-tutorial-as-a-workspace-state.md)).
  - It runs in a private, initially empty workspace (`.quantarium/tutorial/`). While it runs, pages show only what the tutorial made, and a **Tutorial** badge appears in the top bar.
  - Finishing adds its one cell, with that cell's market, configs, and results, to the workspace. The cell's Feed experiment can still be advanced afterwards.
  - Skipping or leaving partway leaves the workspace exactly as it was.
- Fixed tutorial replays in workspaces that already have content:
  - A replay could reuse a Feed experiment name whose database still existed, so its deploy failed. Names now avoid existing cells, scenarios, experiments, configs, and databases.
  - The tutorial got stuck if `strategies/starter.py` had been edited to remove TrendFollower. Edited starter files are now left alone, and the tutorial uses its own copy (`strategies/starter_2.py`).
  - The tutorial got stuck when `quantarium-workspace.json` scanned other scenario folders. The tutorial workspace now always uses the default folders.
  - Skipping a replay partway left an unnamed cell and a scenario behind.
- A tutorial left in progress by v0.2.1 starts again from its title screen.

## v0.2.1 — 2026-10-21

- Rebuilt the tutorial as a first-run quest in the user's own workspace ([ADR-0012](docs/adr/0012-first-run-tutorial-in-the-real-workspace.md)).
  - A title screen opens on a workspace's first launch, followed by five chapters, each with an objectives card: Build a market, Hatch a cell, Train, Enter the arena, The record.
  - The user generates a scenario, creates and configures a cell, trains it in Static, imports it into a practice Feed experiment and advances it, gets a briefing on Live Feed, and reviews Log and Activity.
  - It ends on Home with the cell they made, presented in a finale.
- How the tour plays:
  - Objectives complete when the app shows the action happened.
  - **Do it for me** completes the current one.
  - Progress is saved in `.quantarium/onboarding.json` and resumes after a reload.
  - **Learn → Replay the tutorial** runs it again with new, numbered names.
- Removed the v0.2.0 disposable practice workspace and its API (`/api/v1/tutorial-sessions`, `/api/v1/tutorial/{id}/…`).
- New workspaces start empty:
  - The default scan folders no longer include `examples/`.
  - Starter strategies are copied to `strategies/starter.py` when the tutorial starts or is skipped.
  - Scripts that existing cells use stay in the script picker.
- Scenario-backed Feed deployments accept an optional "Release sessions through" date. The Feed page has a Live Feed briefing panel.
- Fixed a Windows bug in which checking whether a practice session's server was still running sent Ctrl+C to the console, interrupting the server and other processes in it, and reported exited processes as alive.
- Documented the cell library, deploy, holdings, arena, and command endpoints in the API reference.

## v0.2.0 — 2026-10-21

- Added a game-style guided tour: a dimmed screen, one highlighted control, dialogue steps, blocked off-path clicks, Back, and an always available practice exit. The tour follows real pages and controls across Home, cells, Scenario, Static, Feed, Log, Activity, and Workspace.
- Added a persistent **Learn** page and an 11-chapter slide-by-slide handbook with a glossary-style topic catalog and a feature map that distinguishes UI, CLI, and unavailable functions. The circled **?** appears beside page titles and opens the relevant handbook chapter.
- Added disposable tutorial sessions with fictional prices, pinned example strategies, and prepared Simple, 12-stock, and 40-stock cells with simulated results. Practice uses a separate API and workspace; normal cell, Feed, Log, and file lists remain isolated.
- Finishing practice waits for its jobs, removes its files, and returns to the normal app. Startup recovery removes marked sessions left by a stopped server; idle sessions expire after 24 hours.
- Updated the local API and package assets for the tutorial. Practice Feed creation is limited to scenario data; Live Feed remains CLI-only and no broker orders are sent.

## v0.1.1 — 2026-10-20

- Reordered the sidebar to Home, Feed, Scenario, Static, then the remaining pages.
- Added **Add cell** to Static, including its empty state.
- Removed the redundant **Trained** heading and explanatory line above Static results.
- Removed a loading placeholder that remained below the rendered app and added unwanted scroll space.
- Clarified in the README why configuration-defined example cells can appear in Feed without appearing on Home.

## v0.1.0 — 2026-10-20

The first complete version of Quantarium: a local research simulator for comparing independent trading-strategy cells on history, on chosen market scenarios, and in forward tests. It simulates trades only; it never places real orders.

### Cells
- A **cell** is one algorithm with its own simulated account: a **Simple** cell holds one stock with one script; a **Complex** cell holds many stocks and combines scripts (sleeves), each with a share of the money.
- Cells live in a workspace library (`cells/*.json`). They can start as drafts and are versioned once they have results, so earlier results keep describing the settings that produced them.
- Each cell fails, retires, or joins an experiment independently of the others ([ADR-0009](docs/adr/0009-cell-lifecycle-across-modes.md)).

### Modes
- **Static**: fast, repeatable daily replay of a fixed price file. Fills happen at the next session's open, with configurable slippage, commission, and an optional volume cap. Static runs also handle supplied splits and cash dividends, show strategies split-adjusted history, and enforce portfolio limits and rebalance cadence.
- **Feed Batch**: forward testing one completed session at a time, with checkpoints and resumes. First-seen bars are pinned and revisions logged. Sessions that closed before a cell's configuration was frozen are labeled backfill; later ones are labeled forward. Sources: Alpaca daily bars, a hand-maintained price file checked against an offline NYSE calendar, or a scenario for practice runs.
- **Live Feed**: a synchronized single-session simulation from completed one-minute bars, with capture, replay, audit, and tolerance for quiet minutes ([ADR-0001](docs/adr/0001-live-feed-timing-and-replay.md), [ADR-0007](docs/adr/0007-live-feed-quiet-minutes.md)).

### Scenarios and suites
- Named, hash-pinned scenarios: historical slices, seeded synthetic markets, and recorded transformations (crash, volatility, drift).
- Suites run the same cells across scenarios and compare them in a cells-by-scenarios matrix with per-tag averages ([ADR-0010](docs/adr/0010-scenario-inputs-and-suites.md)).

### Data
- `import-bars` turns daily files downloaded from Yahoo Finance, Nasdaq.com, Stooq, or any plain OHLCV export into Quantarium's format, with no API key.
- Optional Alpaca support for daily and minute data, plus a read-only `alpaca-check` preflight.

### Analysis
- Return, drawdown, annualized volatility, Sharpe ratio (zero risk-free rate), turnover, exposure, and benchmark comparison.
- Per-cell timelines, Feed-to-Static export, and event-by-event replay audits.

### App
- `quantarium serve` runs a local app and a versioned JSON API ([docs/api.md](docs/api.md), [ADR-0011](docs/adr/0011-local-api-and-frontend-architecture.md)). It binds only to loopback and requires a per-session token for anything that writes.
- Pages:
  - **Home**: the cell collection as cards.
  - **Feed**: the arena of forward-tested cells, with Advance, Retire, and Import from Static.
  - **Static**: the training ground.
  - **Scenarios**: browse, generate, and transform.
  - **Log**: every run and experiment.
  - **Activity** and **Workspace**.
- A cell's page charts up to 4 holdings with trade markers, shows tiles up to 30, and an aggregate dashboard beyond that ([docs/frontend-spec.md](docs/frontend-spec.md)).

### Known limits
- No request has been made with real Alpaca credentials; that path is tested only against simulated responses. The keyless file-based path is the validated way to use real data.
- Historical results using today's stock lists carry survivorship bias. Point-in-time index membership and delisted-stock prices are not available.
- Synthetic and transformed scenarios show sensitivity to a market shape someone specified, not real market behavior.
- Long-only, whole shares, no shorting, taxes, mergers, spin-offs, symbol changes, or realistic liquidity. Sleeves share one account, so per-sleeve profit is not measured.
- Live Feed covers one session, without gap recovery or multi-session continuity. Strategies are trusted local Python and are not sandboxed.
