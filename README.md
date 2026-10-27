<p align="center"><img src="https://raw.githubusercontent.com/f1ymango/quantarium/main/docs/images/logo.png" alt="Quantarium logo: a Q around three candlesticks, its tail a check mark" width="128"></p>

# Quantarium

**Test a trading idea honestly, on your own or with AI agents, before you believe it.**

**Version: 1.0.7.** See the [changelog](CHANGELOG.md) for its contents and known limits. Licensed for noncommercial use under [PolyForm Noncommercial 1.0.0](LICENSE.md).

Quantarium is a local lab for testing stock-portfolio strategies. It simulates trading and never places real orders. It runs on your computer, listens only on a loopback address, and its core uses only Python's standard library.

> **Quantarium supplies no market data and gives no financial advice.** It ships only invented example prices; bring your own data and check its terms. A result describes a simulation of the tested period under stated assumptions, not what will happen with real money.

![Home: each cell is a strategy with its own simulated account](https://raw.githubusercontent.com/f1ymango/quantarium/main/docs/images/home.png)

**What it is not:** a broker, a data vendor, a source of trading signals, or a high-speed engine. Larger backtesting engines cover more markets and run faster; Quantarium aims at whether a result is evidence at all.

It has three layers:

- **A portfolio simulator.** Each **cell** is one strategy with its own simulated account. Replay it on historical or synthetic prices (**Static**), follow it forward session by session (**Feed**), or run many cells across many scenarios at once.
- **A lab for evidence.** The **Lab** freezes a test before it runs, compares the strategy with simple controls on the same data, and records every attempt, including failures. It refuses fresh verdicts on data an idea has already used, keeps holdout scenarios away from agents, and checks whether a language model could simply remember the market it is tested on.
- **A safe harness for AI agents.** Agent features are off until you turn them on. Each connection has its own permissions and budget. Code an agent writes runs in a sandbox until you review it, and anything consequential waits in an Inbox for your approval. A strategy may consult a model only through a metered gateway that records every answer for replay.

## What makes it different

Backtesting engines aim for speed and market realism; AI trading projects aim to produce decisions. Quantarium aims at a narrower question: **is this result evidence, or did I fool myself?** It is built for the ways strategy research usually goes wrong:

| Trap | What Quantarium does |
| --- | --- |
| Trying many variants and keeping the best | Counts every variant an agent simulates, and reports it beside the result |
| Tuning on the data you then test on | Pre-registers each Lab test and marks reused data as exploratory |
| Beating nothing in particular | Runs buy-and-hold and equal-weight controls on the same data; a tie with a control is not support |
| A model that remembers history | Judges model-driven cells on real data only after the model's training cutoff, on synthetic markets, or on disguised copies of real ones |
| Credit for parts that add nothing | Reruns a cell without its model, without its risk card, and on fixed allocations, to show what each part adds |
| An agent that sees the answer | Holdout scenarios that agents can neither read nor run on; one holdout test per cell version, approved by you |
| An agent with too much reach | Permission tiers, budgets, a sandbox for its code, an audit log, and proposals you approve |

Quantarium's simulator is deliberately modest: daily and one-minute US stock bars, long-only, whole shares, and no shorting or taxes; delistings and index membership are modelled only for research data you import ([modeling boundaries](#current-modeling-boundaries)). Its engine is cross-checked against an established one. Its results describe the tested period and assumptions, not future returns.

![The Lab: a frozen experiment, its controls, and what limits the verdict](https://raw.githubusercontent.com/f1ymango/quantarium/main/docs/images/lab.png)

## Quick start: local app

Use Python 3.11 or newer, in a virtual environment (see the Windows instructions below). Install the released package, or the repository you cloned:

```shell
python -m pip install quantarium
```

```shell
python -m pip install -e .
```

Then serve a workspace, a folder where your cells, runs, and scenarios are saved:

```shell
python -m quantarium serve --workspace .
```

Open **http://127.0.0.1:8765/** on the same computer. The server stays in the terminal until you press Ctrl+C. It listens only on a loopback address; the app and its versioned JSON API are local.

The workspace is the folder Quantarium serves: cells, runs, scenarios, and tutorial progress are saved there. Serving the repository root (`--workspace .`) keeps them in your checkout, ignored by Git. To try Quantarium, or the tutorial, from a clean slate, serve a new, empty workspace instead:

```shell
python -m quantarium serve --fresh
```

Each run makes a new folder under `~/quantarium-workspaces/` and prints its path; keep it or delete it when you are done. `--fresh-root <folder>` (or the `QUANTARIUM_FRESH_ROOT` environment variable) chooses where those folders go.

For a first run without an account or API key:

1. On **Home**, select **Add cell**. Set it to **Simple**, choose symbol `AAA`, and select `BuyAndHold` from `examples/strategies.py`. Save the cell.
2. Select **Run → Replay on data**, then choose `examples/sample_daily.csv`.
3. Open the result on the cell page to inspect its equity, holdings, and timeline. The app saves cell definitions under `cells/`, generated configs under `configs/generated/`, and results under `runs/`.

`AAA` and the example prices are fictional. This verifies the workflow, not a strategy's real-market performance. For real daily data without credentials, start with [import-bars](#real-data-without-an-api-key). The [local app section](#local-api-server) explains its pages and workspace; the [frontend specification](docs/frontend-spec.md) records what v0.1.0 includes and defers.

## Quick start: command line

The same simulator is available without the app:

```shell
python -m quantarium run examples/experiment.json
python -m quantarium report runs/example.sqlite
python -m unittest discover -s tests -v
```

The sample runs a Simple buy-and-hold cell and a Complex cell with equal-weight and recent-leader sleeves. For more examples:

```shell
python -m quantarium run examples/action_experiment.json
python -m quantarium analyze static runs/action_example.sqlite
python -m quantarium run examples/policy_experiment.json
python -m quantarium report runs/policy_example.sqlite
```

The action example applies a split and a dividend. The policy example reserves 20% cash and rebalances a two-stock basket every three sessions.

## A seven-minute tour

1. Serve an empty workspace with `python -m quantarium serve --fresh` and open **http://127.0.0.1:8765/**.
2. Play the six-chapter tutorial (about seven minutes). It builds a fictional market and cell, runs a Static replay and practice Feed experiment, and walks through two Lab attempts on the same data.
3. Finish the tutorial to keep the cell, market, Static result, and Feed practice. The tutorial's Lab attempts are practice only and are discarded.

To try the agent features, see [Agent features](#agent-features-off-by-default).

## Windows: `Python was not found`

PowerShell may find the Microsoft Store **app execution alias** before a real Python installation. The `py` command can also be only an alias, so switching to `py` is not always a fix. Check the commands in order:

```powershell
Get-Command python, py -All | Select-Object Name, Source
```

Create a virtual environment **once** with a real Python 3.11+ executable outside `Microsoft\WindowsApps`:

```powershell
& 'C:\full\path\to\python.exe' --version
& 'C:\full\path\to\python.exe' -m venv .venv
```

Then activate it once in each new PowerShell window. Standard Windows Python puts the script in `.venv\Scripts`; MSYS2 Python puts it in `.venv\bin`. Use the path that exists:

```powershell
.\.venv\Scripts\Activate.ps1
# Or, for an MSYS2-created venv:
.\.venv\bin\Activate.ps1
```

After activation, `Get-Command python` should point inside `.venv`, and the ordinary `python -m quantarium serve --workspace .` command works. Install the package once with `python -m pip install -e .` (or `-e '.[live]'` for Live Feed); the installation supplies `tzdata` for New York market time. The local `.venv` is ignored by Git and must be created separately on each computer.

## Core capabilities

- **Independent cells:** separate accounts, decisions, orders, and SQLite event trails; Complex cells combine weighted strategy sleeves. A cell can fail, retire, or join a Feed Batch experiment without stopping the other cells. The workspace library versions a cell after deployment so previous results retain their settings.
- **Reproducible daily simulation:** decisions use data through the current close and fill at the next available open. Whole-share long-only orders support slippage (flat, or growing with the previous session's range), commission (per order, per share, and a minimum), rebalance cadence, target limits, and an optional previous-session volume cap. Supported splits and cash dividends use explicitly supplied raw-bar actions.
- **Scenario studies:** package historical slices, seeded synthetic markets, and recorded transformations; run the same cells across scenarios in a suite and compare a cells-by-scenarios matrix.
- **Forward testing:** Feed Batch pins first-seen daily bars, resumes from checkpoints, marks backfill versus forward sessions, and accepts CSV, scenario, or Alpaca data. Live Feed shares one bounded minute history across independent cells; it supports capture, replay, quality checks, and quiet-minute tolerance.
- **Inspection:** analyze returns, drawdown, volatility, Sharpe ratio, turnover, exposure, and benchmark-relative results; inspect per-cell timelines, export Feed data for Static replay, audit saved replays, or generate an offline HTML dashboard.
- **Local app and API:** Home, cell configuration and deployment, Feed and Static views, Scenarios, Lab, Log, Activity, Workspace, Agents, and Inbox. Script discovery parses files without executing them; running a chosen script executes trusted local Python code.
- **Model assisted cells:** a data-only risk card can add a daily drop-risk estimate to a cell's context and journal. The Lab pins the card and uses its last training-label date when judging a run.

The app does not yet control a Live Feed session; use the [Live Feed CLI](#live-feed-with-alpaca). No real Alpaca account session has been validated for this release. Historical index claims need point-in-time membership and delisted-stock data, which you can [import](#your-own-research-data).

## Lab experiments

The **Lab** page registers a test before it runs. Select one configured cell and one or more scenarios, enter an idea ID, simulated costs, and a pass rule. Registration freezes the cell, direct script files, and scenario contents. Select **Run frozen experiment** to compare the cell with buy-and-hold and daily equal-weight controls on the same data. The ledger keeps the registration, attempt count, report, verdict, and caveats, including failures. Its files are local under `.quantarium/` and ignored by Git.

A result is **supported within tested scope**, **not supported**, **inconclusive**, or **unknown**. Each result also reports trial-aware statistics: the **probabilistic Sharpe ratio** (the chance the candidate's true Sharpe ratio is above zero, given its sessions and the shape of its returns), the **deflated Sharpe ratio** (the same, against what the best of all the attempts at this idea would reach by luck), and, after four attempts at an idea on shared data, the **probability of backtest overfitting**. A candidate that beats its controls is supported only if its deflated Sharpe ratio is at least 0.95 in every scenario; otherwise it is inconclusive.

To share a result, write its **evidence report**: one self-contained HTML page with the verdict, every caveat, the rows and statistics, and everything frozen at registration, by SHA-256. It loads nothing from the network. Check a report against the workspace it came from:

```shell
python -m quantarium lab report l_0123456789ab
python -m quantarium lab verify-report runs/l_0123456789ab-evidence.html --workspace .
```

Without `--workspace`, only the page's own consistency is checked: someone without your workspace can tell the page was not edited, not that the runs happened as shown. Reusing related scenario data for the same idea is exploratory and cannot earn a fresh verdict. A cell's typed `information_end` date is self-declared; a saved model card binds that date to a training record. If any tested scenario begins on or before its final training label, the whole-run verdict is `unknown`. The Lab cannot count tests performed outside this workspace or prove a card's claimed data source or historical index membership. The registered result describes the named period, symbols, controls, costs, and execution model, not future performance. See [ADR-0031](docs/adr/0031-lab-pre-registration-and-evidence.md) and [ADR-0032](docs/adr/0032-data-only-risk-model-cards.md).

The Lab's **Supercells** view lists every cell that uses a risk card, asks a model, or has an allocator, with the evidence its latest experiment found and a button to test it. Agents with the propose permission can suggest a registration through `quantarium mcp`; the user approves it in the Inbox and runs it separately. Holdout scenarios are excluded from the Lab.

## Switch between strategies

A Complex cell can split its money with an **allocator** instead of fixed shares, chosen in the cell's **Allocation** panel. The built-in **Trend regime** gives everything to one script while a symbol closes above its moving average and to another otherwise. Your own allocator is a class with `allocate(context, sleeves)` that returns one share per script, from 0 to 1 and totalling at most 1. The Lab also runs such a cell on fixed shares, to show what the allocator adds ([ADR-0036](docs/adr/0036-composite-and-regime-supercells.md)).

## Let a strategy ask a language model

A reviewed script can ask a model for a decision with `context.ask(prompt, schema)`, where the schema is a small JSON schema the answer must match, for example a choice between `hold` and `invest`. Grant it on the cell's Configure tab (Advanced → Model access), or through `cell-ask-grant` ([API](docs/api.md), [ADR-0034](docs/adr/0034-ask-gateway-and-tape.md)): a model API connection, the script's SHA-256, budgets, and a decision rhythm. Quantarium makes each call without tools, checks the answer, and keeps it on the cell's tape. Switch the grant to `replay` to rerun a backtest from the tape at no cost. A grant's prices come from Quantarium's dated price table (`quantarium/assets/agents/prices.json`, from the providers' pricing pages) unless you enter your own; a model the table does not list needs them entered, and models on your own computer cost nothing. A model API connection can also have a **daily cost limit**, counted across every cell that uses it. A model may remember real history, so the Lab judges such a cell on real history only after the model's training cutoff, which you record on its connection on the Agents page. It also runs the cell without the model to show what the model adds, and can rerun it on disguised copies of real markets ([ADR-0035](docs/adr/0035-llm-and-hybrid-supercells.md)).

## Judge decisions made elsewhere

The [referee guide](docs/referee.md) covers the formats, the decider protocol, exporting from other systems, and what each way in can prove.

Quantarium is becoming a referee for decisions made outside it, by another framework, a language-model trading agent, or a spreadsheet ([plan](docs/plans/v0.6.x-referee.md)). The first step is the decision file: JSON (`quantarium-decisions-v1`) with dated target weights or orders, or a CSV with the columns `date,symbol,weight`. Each decision is made at its date's close and fills at the next open.

```shell
python -m quantarium decisions check my_decisions.csv
```

The check validates the file and prints its summary and SHA-256. Put decision files in the workspace's `decisions/` folder. On a cell's **Configure** tab, choose **A decision file** to replay one on Quantarium's engine, with its fills, costs, and portfolio rules; a date without a decision keeps the holdings. A file made after the fact may use hindsight, so the Lab runs it against its controls but caps its verdict at **unknown**: the result is for exploration, not evidence. 
For a verdict that counts, submit decisions **before** their sessions to a forward inbox. Quantarium stamps when each one arrives, and a decision counts only if it arrived before the next session opened (09:30 New York), so it cannot use hindsight:

```shell
python -m quantarium decisions inbox-create my_agent --symbols AAA,BBB
python -m quantarium decisions submit my_agent --date 2030-01-07 --weights AAA=0.5,BBB=0.3
```

Agents with the simulate permission can do the same through `quantarium mcp` (`create_decision_inbox`, `submit_decision`). Choose the inbox on a cell's Configure tab, and Feed Batch follows it forward. The Lab judges an inbox cell in full, on scenarios of real prices covering its dates; registration freezes the inbox as it stands.

A **decider** is an outside program that Quantarium runs session by session, showing it only data up to each session: for example a wrapper around another framework or a language-model trading agent. Register one yourself; agents cannot register, choose, or run deciders, since a decider is your program and may spend your money:

```shell
python -m quantarium decisions decider-create my_agent --env MY_MODEL_KEY --training-cutoff 2025-01-31 -- python my_agent.py
```

The program reads one JSON line per session (`date`, `bars`, new `history`, `cash`, `positions`, `equity`) and answers with `{"weights": {...} or null, "orders": [...], "note": ""}`. It starts in an empty folder and receives only the environment variables you name. Choose it on a cell's Configure tab. Every run records the decider's answers in `decisions/recorded/`, which can be rescored later. The Lab judges a decider like a model-driven cell: on synthetic and disguised markets, and on real history only after its training cutoff.

Your own trading history works the same way. Export your fills as a CSV with `date,symbol,side,shares,price` (other columns are ignored) and turn them into a decision file:

```shell
python -m quantarium decisions from-trades my_trades.csv decisions/my_trades.json
python -m quantarium decisions gap my_trades.csv runs/my_trades.sqlite --cell my_trades
```

Each fill becomes a market order decided at the close before it, so Quantarium fills it at that session's open with your costs; same-day trades in a symbol are netted. The report lists what a trades file cannot show (deposits and withdrawals, dividends, short sales) and suggests starting holdings for shares sold before they were bought. After a replay, `gap` compares Quantarium's fills with your real ones, in basis points. Like any finished decision file, the Lab caps its verdict at **unknown**: it shows your costs, drawdowns, and how you did against simple controls, not whether your method works.

## Train and inspect a risk model card

The first ML capability estimates the chance of a greater than 5% drop during the next five completed sessions, measured from the next open to the lowest low. It uses only two price features available at the decision close: 20-session return volatility and 14-session average true range. This is an experimental risk estimate, not an order rule. It is not a calibrated probability guarantee and does not improve a strategy by itself.

Train on a Static-format daily CSV. `--through` limits the last *outcome date* admitted to training; a feature row whose label ends later is excluded. Declare the source honestly. The child process reads the CSV, fits the model, and writes a content-addressed JSON card to `models/`:

```shell
python -m quantarium ml train-risk examples/scenarios/bull_with_crash/prices.csv --through 2030-12-31 --source example_scenario
```

For a real dataset, replace the file and source. The optional `pip install -e '.[ml]'` installs NumPy and scikit-learn for a faster fit on Python versions with compatible wheels. Without that extra, a deterministic standard-library fit works for smaller studies. Neither library is needed to load a card or run a cell. Training is CPU work in a separate process; large inputs can take time and memory.

In the app, open a cell's **Configure → Advanced** section and select the card. Its `information_end` is filled from the latest training label. At each completed daily close, the engine writes a `model_estimate` event and supplies `context.risk_estimates[symbol]` to scripts when 21 uninterrupted sessions of bars are available. The estimate does not change holdings unless a script explicitly uses it. The Timeline's **Actions and events** view shows the estimates. Home marks the cell **Model assisted** and shows its latest Lab verdict for that cell version, if one exists. Lab registration pins the card's bytes; a changed or missing card invalidates the frozen experiment. Risk-driven trading rules should be registered and compared with the simple controls before treating their results as evidence.

The card stores numeric weights, feature and label recipe, input-file SHA-256, source label, and last training-label date. It is data, not a Python pickle. Quantarium checks the card's content hash and declared boundary; it cannot independently prove that the claimed source supplied the bars, that off-workspace training never happened, or that the data include delisted securities. These daily cards work in Static and Feed Batch; Live Feed minute bars reject them. Copy the card with its exact filename if moving a cell to another workspace.

## Feed Batch with Alpaca

Create Alpaca market-data API credentials and put them in the local environment as `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY`. Keep them out of the JSON config and repository.

Before a first experiment, check what the account can actually supply:

```shell
python -m quantarium alpaca-check feed examples/feed_experiment.json
python -m quantarium alpaca-check live examples/live_experiment.json
```

`alpaca-check` runs no simulation and writes nothing. It reads the trading calendar and the last completed session's daily bars for every configured symbol. It reports missing symbols, and it exits with status 1 when the experiment would fail. For an IEX config it also requests SIP daily history, which Alpaca documents as available without a subscription once it is at least 15 minutes old, and reports whether this account received it. For `live`, it fetches that session's historical minute bars. It reports each symbol's longest run of minutes without a bar, a starting point for `max_quiet_minutes`. Historical bars can differ from what the stream delivers on the day. It also warns when an IEX subscription exceeds the Basic plan's documented 30-symbol stream limit; Quantarium cannot see which plan the account has. Credentials never appear in its output.

Then, from the repository root:

```shell
python -m quantarium feed-batch examples/feed_experiment.json
python -m quantarium feed-report runs/feed_demo.sqlite daily_demo
python -m quantarium feed-batch examples/feed_actions_experiment.json
```

The [feed example](examples/feed_experiment.json) uses AAPL and MSFT on the IEX feed. Set `start_date` to the first date you want to include. Initial runs from an old date are **historical backfills**; observations become a forward test only as subsequent market sessions arrive. To stop at a particular completed New York date, add `--through YYYY-MM-DD`. Quantarium always caps processing at the *previous* New York calendar date to avoid a partial current-day bar. Run the command again after new daily bars are available. No process needs to stay running.

Alpaca returns historical bars in pages, sometimes with only one symbol on an early page. Quantarium follows all pages, batches requests in groups of 50 symbols, and checks the returned days against Alpaca's [US trading calendar](https://docs.alpaca.markets/us/reference/legacycalendar). Missing complete market sessions and sessions missing any configured symbol stop the batch **before new days are simulated**; holidays and weekends are skipped. A missing symbol may reflect a halt, delisting, new listing, or feed coverage issue. Investigate it and start a new experiment with an appropriate universe or data source; Quantarium does not invent a price or execute an order on that day. The calendar endpoint uses Alpaca's paper trading API, so the configured credentials must have access to both market data and the paper API. Alpaca currently documents calendar coverage from 1970 through 2029; Quantarium rejects dates outside that range. The sample uses IEX, a single-exchange feed; SIP has broader market coverage but may require a paid entitlement. Data scope and historical access depend on your Alpaca plan. See [Alpaca's historical bar reference](https://docs.alpaca.markets/us/reference/stockbars) and [market-data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq).

Each Feed batch experiment has a `feed_id` and SQLite file. The experiment settings (everything but `cells`), each cell's configuration, and strategy source hashes are pinned. Changing any of them requires a new `feed_id` and database. Adding a cell with a new name is allowed; see [Cell lifecycle](#cell-lifecycle). Strategy classes with internal state must implement both `snapshot()` returning JSON-compatible state and `restore(state)` to resume correctly; `AlternatingDays` in the example shows the contract. Strategies using only `context.history` need neither. The event trail and original bars remain inspectable in SQLite; `feed-report` reads summaries without loading strategy code or credentials.

To freeze the bars already saved by a Feed experiment for rapid Static replay:

```shell
python -m quantarium export-feed runs/feed_demo.sqlite daily_demo runs/captured.csv
python -m quantarium export-feed runs/feed_demo.sqlite daily_demo runs/captured.csv --through 2026-10-16 --overwrite
```

The command reads SQLite without credentials or a network request. It writes `captured.csv` in the Static OHLCV format, `captured.actions.csv` with pinned supported corporate actions, and `captured.csv.manifest.json` with their SHA-256 hashes and source details. Use the price CSV as `data_path` and the action CSV as `actions_path` in a new Static config whose cells request only exported symbols. If Feed recorded a later bar revision, the export still uses its first-seen bar. `--through` selects saved bars on or before that date; it also includes dividends earned by the selected final day even when payment is later. `--overwrite` is required to replace any of the output files. The action CSV is empty when the Feed had no pinned actions. It cannot establish that a provider's action history was complete, especially for a Feed experiment using `ignore_corporate_actions` or one that predates action pinning.

After running a Static config with the same cells, strategies, transaction costs, exported bars, and actions, compare its saved event trail with the entire Feed experiment:

```shell
python -m quantarium audit-replay runs/feed_demo.sqlite daily_demo runs/replay.sqlite
python -m quantarium audit-replay runs/feed_demo.sqlite daily_demo runs/replay.sqlite --run-id STATIC_RUN_ID
```

The command returns `matched: true` when account valuations, targets, orders, corporate actions, and strategy provenance agree. It ignores only the signal field that says whether a next session is available: Static knows the tape's end, while Feed does not. A mismatch is printed with the first differing events and exits with status 1. The audit checks the **entire** saved Feed range, so a replay from a `--through` export made before the latest Feed session will differ.

Feed batch checks Alpaca's [corporate-actions endpoint](https://docs.alpaca.markets/us/reference/corporateactions-1) before fetching new bars. By default, **any** reported action with an ex-date on or after the experiment's start for a configured symbol stops the batch before new sessions; earlier committed sessions remain available. The guard also stops on records without an ex-date because their relevance is unknown. A new experiment can set `"apply_supported_corporate_actions": true` to apply US-dollar cash dividends and forward or reverse splits, as shown in [the action-aware Feed example](examples/feed_actions_experiment.json). Both modes fetch action records by process date beginning 366 days before the experiment. The apply mode pins each applied action's ID and terms, and stops if a pinned action changes or disappears, an action arrives after its ex-date was already simulated, a split would create fractional shares, or an unsupported event appears. Ex-dates and payment dates must be market sessions when they affect the run. The `feed-report` includes `actions_pinned`.

To study raw prices despite known action distortions, set `"ignore_corporate_actions": true` in a **new** Feed config with a new `feed_id` and database. It bypasses the check and does not apply actions. The two options cannot be combined. Alpaca warns that action data may be published late; Quantarium can stop a later batch when it discovers a missed event, but cannot repair earlier saved valuations. The 366-day process-date lookback is also a data boundary. Market-data entitlements for this endpoint have not been verified with a live account. No live trading account is touched.

## Real data without an API key

Download each symbol's daily history from a site's own export (Yahoo Finance's historical-data download, Nasdaq.com's historical quotes, or Stooq's download link in a browser), then merge the files into a Static price CSV:

```shell
python -m quantarium import-bars data/tape.csv AAPL=downloads/AAPL.csv MSFT=downloads/MSFT.csv
```

The layout is detected from the header: `yahoo`, `nasdaq` (`$` prices and `MM/DD/YYYY` dates), or `stooq`/`plain` (`Date,Open,High,Low,Close,Volume`). Rows with blank or `null` prices are skipped. Every symbol must have the same dates. `--common-dates` instead keeps only shared dates and reports how many were dropped. A `tape.csv.manifest.json` records each source file's hash and layout.

Download sites usually publish **split-adjusted** prices, so do not add split actions to imported bars. By default dividends are left out, giving a price return. `--price-basis adjusted` (Yahoo only) rescales each bar by `Adj Close / Close` to approximate total return. Use the result directly as a Static `data_path`, or cut periods such as 2008 or 2020 into scenarios with `scenario slice`. Survivorship bias still applies to symbols picked from today's lists.

For a forward test on real data, point Feed Batch at the file and append newly downloaded days:

```json
{"feed_id": "my_forward_test", "provider": "csv", "data_path": "data/tape.csv", "start_date": "2026-10-21", ...}
```

The file is reread on every `feed-batch` run. Expected sessions come from an offline NYSE calendar covering regular holidays since 1998 and known special closures, up to the file's last date. A forgotten download therefore stops the run with `Missing complete market sessions`, and is not skipped. The usual Feed rules still apply: first-seen bars are pinned, later changes are logged as revisions, sessions are labeled backfill or forward, and processing stops at yesterday's New York date. Unscheduled closures announced after this release are not in the calendar.

## Your own research data

Quantarium supplies compatibility, not data. Research data you bought or gathered, with delisted securities and index membership where you have them, goes into one folder in [Quantarium's documented format](docs/data-format.md): `source.json` (the supplier, a license note, and your survivorship declaration), `prices.csv` (symbols may list late or stop early), and optionally `delistings.csv` and `membership.csv`. Check it and make a scenario from it:

```shell
python -m quantarium data check examples/data/fictional_index
python -m quantarium data import examples/data/fictional_index scenarios/fictional_index --name fictional_index
```

The Workspace page lists data folders under `data/` and imports them with one click. The scenario records where the data came from and what you declared about survivorship. The example folder holds an invented index with a late listing, a bankruptcy, and a takeover.

![An imported scenario: an invented index whose members list, go bankrupt, and are taken over](https://raw.githubusercontent.com/f1ymango/quantarium/main/docs/images/scenario.png)

Suites and the Lab replay imported scenarios as a **changing universe** ([ADR-0039](docs/adr/0039-changing-universes.md)): a symbol trades from its first bar to its last, a holding in a delisted symbol is paid out at the delisting price (or its last close), and a cell with `"index": "FIC5"` holds only that index's members as of each close. A Static config switches this on with `"changing_universe": true`, `delistings_path`, or `membership_path`. Strategies see only the symbols they may hold in `context.bars`, so existing strategies work unchanged; weight on anything else stays in cash and is recorded as `outside_universe`.

The Lab reads the declaration. A verdict on real-price data not declared survivorship-free says so, and so does a candidate that trades a fixed list of symbols on real prices, since a list chosen today knows which companies survived. A cell with `index` avoids the second caveat, and its controls hold the same members as of each date.

## Futu OpenAPI market data

Futu (moomoo) daily bars come in through a bridge that runs in its own Python environment, so Quantarium gains no dependency: the bridge asks your local Futu OpenD gateway for candles and writes a Quantarium price file. It fetches market data only and has no trading code. Setup and use are in [docs/futu-bridge.md](docs/futu-bridge.md).

## Live Feed with Alpaca

Live Feed is an experimental, single-session forward simulator. Install the optional WebSocket dependency, set market-data credentials, and edit the example's `feed_id`, `start_at`, and `stop_at` for the next regular New York market session. The timestamps must include a timezone and must cover one session; use the actual early close time when applicable. Start the command before `start_at`:

```shell
python -m pip install -e ".[live]"
python -m quantarium live-feed examples/live_experiment.json --capture runs/live_capture.jsonl
python -m quantarium live-report runs/live_demo.sqlite live_demo_20261020
```

Live Feed reads `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` from the environment. The [example](examples/live_experiment.json) subscribes to AAPL and MSFT on Alpaca IEX. The subscription contains the union of all cell symbols, so overlapping cells receive the same market bar through one connection. Alpaca may limit the number of subscribed symbols and connections for an account; a rejected subscription stops the command. The adapter subscribes to both `bars` and `updatedBars` on Alpaca's [stock stream](https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data) and uses the [WebSocket protocol](https://docs.alpaca.markets/us/docs/streaming-market-data). This command only consumes market data; it does not call Alpaca's trading API.

`--capture` is optional. It creates a new JSONL file and refuses to overwrite an existing one. The file contains a session header, incoming market frames after subscription, and local clock ticks used to finalize bars; it does not contain the authentication messages or API keys. Choose a new capture path if you restart the live command. The capture grows through the session, including clock ticks while no market frame arrives, and may end before the session if the command is interrupted.

Each strategy sees a complete one-minute bar after a 95-second finalization delay (configurable from 95 through 119 seconds). This allows the stream's usual late-trade update at about 90 seconds after the bar's start to be included before the bar is pinned. A signal may fill only at the open of a minute that starts **after** the signal became available. For a bar beginning at 09:30, finalized at 09:31:35, the earliest eligible fill is the 09:32 bar's open; that simulated fill is confirmed once the 09:32 bar arrives. Revisions after finalization are logged without changing prior decisions. This is a bar-based paper fill model, not a claim that an actual order would receive that price.

Alpaca publishes a minute bar only when the symbol traded in that minute, and IEX sees a small share of US trading, so quiet minutes are routine for less active symbols. `max_quiet_minutes` (default `0`) sets how many consecutive minutes a symbol may go without a bar. With the default, any missing bar halts the experiment before processing that minute. With a positive value, a quiet symbol:

- keeps its last real bar in `context.bars`, with that bar's earlier `timestamp`, and is listed in `context.quiet_symbols`;
- is valued at that bar's close, and its `history` does not gain a bar for the quiet minute;
- receives no fill that minute: its order is journaled with `filled_shares: 0` and `unfilled_reason: no_trade_in_interval`, and the next signal decides again.

A symbol that has not traded yet in the session is absent from `context.bars`; a target for it is journaled as `no_price_observed`. The experiment halts once any symbol has been quiet for more than `max_quiet_minutes`, counting from the session start for a symbol never seen. Quantarium does not synthesize zero-volume bars or interpolate prices. Run `capture-quality` on a recorded session: its `longest_quiet_run_minutes` is the smallest setting that would not have halted. A minute that finalizes too late for a live decision still halts. The reasoning is in [ADR-0007](docs/adr/0007-live-feed-quiet-minutes.md). Unexpected stream disconnects also halt after any minute has been committed. Resume is possible only while the declared minute sequence is still complete; otherwise start a new experiment with a new `feed_id`. The current version does not backfill a gap, carry positions across sessions, or apply corporate actions. Use Feed Batch for ongoing multi-day forward testing.

If one Live Feed strategy fails, its cell stops at the last completed minute with `status: failed` and an error in `live-report`; its `cell_failed` event appears in the timeline. Other cells keep processing shared bars and can continue after a restart. When every cell has failed, the experiment halts. Market-data and database failures remain experiment-level failures. Live analysis includes failed cells with their shorter curves and compares them with a benchmark only on matching minutes. The failure boundary and alternatives are recorded in [ADR-0002](docs/adr/0002-live-cell-failure-isolation.md).

The cell status is `active` during a running experiment, `completed` at a normal session end, `stopped` when a market-level failure halts an otherwise healthy cell, or `failed` when that cell's strategy stops. Previously saved Live Feed databases are displayed with the same effective end statuses.

`history_limit` bounds each symbol's in-memory minute history (observed bars, so quiet minutes do not consume it), shared by all cells; all finalized bars and per-cell events remain in SQLite. Strategies receive `context.timestamp` (UTC minute start), `context.date` (New York date), and the same `bars`, `history`, `cash`, `positions`, and `equity` fields used in daily modes. `rebalance_every_sessions` counts **completed minutes** in Live Feed. The final signal can remain unfilled if the declared session ends before its eligible fill. Each run is pinned to its config and strategy source hashes; changing either requires a new `feed_id`.

After or during a session, inspect the minute performance and event trail without credentials:

```shell
python -m quantarium analyze live runs/live_demo.sqlite --id live_demo_20261020 --benchmark live_single --csv runs/live_curve.csv
python -m quantarium timeline live runs/live_demo.sqlite live_basket --id live_demo_20261020 --from 2026-10-20T13:30:00Z --through 2026-10-20T14:00:00Z --csv runs/live_timeline.csv
```

Live analysis reports minute returns, drawdown, and order activity. Its CSV uses UTC `timestamp` and `minute_return_pct` columns. The timeline groups each cell's saved valuations, signals, and orders by minute.

### Offline Live Feed replay

Use the same minute engine without credentials or waiting for a market session:

```shell
python -m quantarium live-replay examples/live_replay_experiment.json examples/live_minute_frames.jsonl
python -m quantarium live-report runs/live_replay_demo.sqlite live_replay_demo
python -m quantarium analyze live runs/live_replay_demo.sqlite --id live_replay_demo --benchmark live_single
```

The example frames and prices are synthetic. A manually prepared replay file is UTF-8 JSON Lines: each line has a timezone-aware `received_at` and an `items` array of Alpaca-style message objects. The sample includes initial bars (`T: "b"`), an update before finalization (`T: "u"`), and a later revision. Frame receive times must be chronological. For this headerless format, replay advances the clock to each minute's deadline between frames, so missing bars halt at the same point as a synchronized run. The summary records `input_kind` and the input file's SHA-256; a changed file cannot resume the same `feed_id`. Use a fresh `feed_id` and database path for another run, including a second run of this example. Replay validates Quantarium's timing and accounting behavior; it does not establish the completeness, entitlement, or quality of any real market feed. The design choices and alternatives are recorded in [ADR-0001](docs/adr/0001-live-feed-timing-and-replay.md).

To replay a `--capture` file, make a copy of the live config with a new `feed_id` and `database_path`, then pass the capture path to `live-replay`. Wait until the capture file is closed, or replay a fixed copy; Quantarium detects a file that changes while it is being read. Captured files include a session header and actual clock ticks, so replay uses the recorded finalization times and can finish with `status: active` if the capture ended early. The replay config must match the captured session time, market feed, symbols, and finalization delay. The header's source `feed_id` may differ from the replay `feed_id`. A manually prepared file without a capture header uses the ideal deadline clock shown in the synthetic example.

Check a capture's market coverage and timing without running any strategies or writing SQLite:

```shell
python -m quantarium capture-quality examples/live_replay_experiment.json examples/live_minute_frames.jsonl
python -m quantarium capture-quality examples/live_experiment.json runs/live_capture.jsonl
```

The report counts the symbol-minutes seen before each finalization, missing and late-only bars, each symbol's longest run of consecutive missing minutes, and updates received before or after finalization. For a recorded capture that ends early, it assesses only minutes whose clock deadline was reached; `window_complete: false` means the rest of the declared session was not observed. A headerless replay file assumes ideal deadlines for the full declared interval. The report does not infer a zero-volume bar or decide why data was absent. The [assessment-window decision record](docs/adr/0006-capture-quality-assessment-window.md) explains this distinction.

For a completed live session and a completed replay of its capture, compare the saved bars, revisions, cell checkpoints, and entire per-cell event trail:

```shell
python -m quantarium audit-live-replay runs/live_demo.sqlite live_demo_20261020 runs/live_capture.jsonl runs/live_replay.sqlite replay_20261020
```

`matched: true` means those saved records agree. The command verifies the capture hash and source session header, reports up to 20 differences, and exits with status 1 on a mismatch. It requires both experiments to be complete. This checks deterministic processing of the captured data, not whether the provider supplied every market event or whether a real order could have filled at the simulated price.

## Static corporate actions

Add `"actions_path": "actions.csv"` to a Static config. Paths are relative to that config. The file must have `date,symbol,kind,value,ex_date` columns and may include `source_id` to distinguish multiple dividends on one date. Quantarium currently accepts:

- `split`: `date` and `ex_date` are the split session; `value` is new shares per old share, such as `2` for a two-for-one split. Shares change before that session's open orders fill.
- `dividend`: `ex_date` is the entitlement session and `date` is the payment session. `value` is cash per share. Shares held at the close before `ex_date` earn a receivable; the receivable counts in equity on the ex-date and becomes cash by the payment session's close. Opening orders on the payment session cannot spend it.

Use **raw, unadjusted** price bars with the action file. Split-adjusted or total-return-adjusted bars would double-count actions. The account and journal stay on raw prices, but `context.history` shows strategies bars from before each applied split divided by its ratio, with volume multiplied. A 2-for-1 split therefore does not look like a 50% drop to a momentum signal. The current bar in `context.bars` is always raw. Dividends are not adjusted. The previous-volume fill cap uses the same split-adjusted volume. Feed Batch saves applied splits in its cell checkpoints; checkpoints written before this change show earlier splits unadjusted ([ADR-0008](docs/adr/0008-split-adjusted-strategy-history.md)). Split ex-dates and dividend ex-dates must have a bar in the tape; dividend payment may fall after the final bar, leaving a receivable in final equity. In-range payment dates must have a bar. The example files are [prices](examples/action_prices.csv), [actions](examples/actions.csv), and [config](examples/action_experiment.json). Quantarium hashes both CSV files for the saved run. It rejects splits that would create fractional shares because cash in lieu is not modeled. Multiple dividends for one symbol on a date require distinct `source_id` values. Mixed or multiple splits on one ex-date are rejected. With no `actions_path`, Static replay uses the original price-only behavior.

## Scenarios and suites

A **scenario** is a named, hash-pinned input: a directory with `scenario.json`, a `prices.csv` in the Static format, and an optional `actions.csv`. Scenarios let one strategy be judged under chosen market conditions, such as a rise, a crash, or a recession. A **suite** runs one set of cells across several scenarios and compares them.

```shell
python -m quantarium suite run examples/suite.json
python -m quantarium suite report examples/suite.json --csv runs/suite_matrix.csv
```

The example suite runs buy-and-hold, a 20-session trend follower, and a two-sleeve basket across four example scenarios in [examples/scenarios](examples/scenarios). The report gives a cells-by-scenarios matrix with these metrics:

- total return
- maximum drawdown
- annualized volatility
- Sharpe ratio (zero risk-free rate)
- turnover
- average exposure
- excess return against the suite's `benchmark` cell

It also averages each cell by scenario tag and states the caveats that apply to the kinds of scenario included.

There are three ways to make a scenario. Each writes the same format, so the engine treats every scenario like any Static input:

```shell
python -m quantarium scenario slice runs/captured.csv scenarios/covid_crash --from 2020-02-03 --through 2020-04-30 --name covid_crash --tag crash
python -m quantarium scenario generate examples/scenarios/specs/steady_bull.json scenarios/steady_bull
python -m quantarium scenario transform scenarios/steady_bull examples/scenarios/specs/bull_with_crash.json scenarios/bull_with_crash
python -m quantarium scenario show scenarios/bull_with_crash
```

- **`slice`** cuts a date range from real daily bars, such as an `export-feed` CSV from a Feed Batch backfill, and keeps actions whose ex-date falls inside the range. The result has kind `historical`. Alpaca stock history starts in 2016, so 2020 and 2022 are reachable from it but 2008 is not.
- **`generate`** builds a seeded `synthetic` market from a JSON spec ([example](examples/scenarios/specs/recession_bear.json)). Its segments set annual drift and volatility. Symbols move together through a shared market factor with per-symbol `beta` and idiosyncratic volatility. Sessions are weekdays with no holidays. The same spec and seed produce the same file.
- **`transform`** applies recorded operations to an existing scenario and produces kind `transformed`:
  - `shock`: a drawdown over N sessions from a date, with optional partial recovery;
  - `scale_volatility`: multiply each return's deviation from the mean;
  - `add_drift`: add an annual drift.
  
  Each operation multiplies a symbol's whole bar by one factor, so bars stay valid and volume is unchanged. Scenarios with corporate actions cannot be transformed.

A suite lists scenarios by path. `symbol_map` renames a scenario's symbols to the cells' symbols, as the example does for `recession_bear`'s `S1` and `S2`. A suite refuses to start if a scenario lacks a symbol the cells need. Each scenario becomes one Static run in the suite's database, tagged with the scenario's manifest. `report` uses the latest run per scenario and refuses a scenario whose files changed after that run. Scenario files are verified against their manifest hashes on every load.

Feed Batch can also read a scenario instead of Alpaca. Set `"provider": "scenario"` and `scenario_path`, as in [the example](examples/feed_scenario_experiment.json), then release it in steps with `--through`:

```shell
python -m quantarium feed-batch examples/feed_scenario_experiment.json --through 2030-03-01
python -m quantarium feed-batch examples/feed_scenario_experiment.json
```

This exercises checkpoints, resumes, retirement, late-added cells, and stateful strategies on chosen conditions without credentials. Scenario dates may lie in the future, and without `--through` the whole scenario is released. Analysis labels every session `phase: scenario` and reports no forward sessions. Scenarios with corporate actions are not supported here.

Results from synthetic and transformed scenarios show how sensitive a strategy is to a market shape someone specified. They are not evidence of how it behaved in a real recession or crash. The design is in [ADR-0010](docs/adr/0010-scenario-inputs-and-suites.md).

## Cell lifecycle

Cells fail independently in every mode. If a strategy raises an exception, or a cell breaks a portfolio limit or meets a split that would leave fractional shares, that cell's partial session is discarded. The cell is marked `failed` with the error, a `cell_failed` event is recorded, and the other cells continue. A Static run still finishes `completed`; `report` shows each cell's `status`, `error`, and final equity at its last completed session. Market-data, database, and strategy-loading errors still stop the whole run.

Stop a Feed Batch or Live Feed cell on purpose without affecting the rest:

```shell
python -m quantarium retire-cell feed runs/feed_demo.sqlite daily_demo equal_weight --reason "replaced by v2"
```

The cell keeps its history and its account as of the last processed session or minute, and gains a `cell_retired` event. Retirement cannot be undone. It is safe while `live-feed` is running; the runner skips the cell from the next minute, and halts if no active cells remain. Feed Batch stops fetching symbols that only retired or failed cells use.

To add a cell to an existing Feed Batch experiment, append it to the config's `cells` with a new name and run `feed-batch` again. The new cell starts after the last processed session, appears in `feed-report` with `joined_after`, and can read earlier saved bars as history. Editing or removing an existing cell, or changing any other setting, is refused. An experiment created before this feature must be resumed once with its unchanged config first. A new cell cannot use a symbol whose saved history has gaps left by a retired cell.

Analysis compares a cell with a benchmark only over the times both cover. Aggregate excess return is left blank when their spans differ, and a late cell's benchmark index is rebased to the cell's first valuation. Static replay cannot reproduce retired or late-added Feed cells, so `audit-replay` lists them as mismatches. The design is in [ADR-0009](docs/adr/0009-cell-lifecycle-across-modes.md).

## Performance analysis

Analyze a completed Static run or an ongoing Feed Batch or Live Feed experiment from its saved SQLite journal:

```shell
python -m quantarium analyze static runs/example.sqlite --benchmark single_stock --csv runs/static_curve.csv
python -m quantarium analyze feed runs/feed_demo.sqlite --id daily_demo --benchmark single_stock --csv runs/feed_curve.csv
```

Static analysis uses the latest completed run unless `--id` selects a specific run. Feed Batch and Live Feed analysis require their `feed_id` via `--id`. The command prints per-cell bar count, total return, maximum drawdown, filled and submitted order counts, total traded notional, and commissions. `--csv` exports an equity curve with an index normalized to 100 at initial cash, per-bar return, drawdown, and benchmark-relative percentage points. Cells with different initial cash can therefore be compared on a common scale. The first return is measured from initial cash; drawdown uses a running peak that includes initial cash. A Feed Batch experiment awaiting its first session reports `waiting_for_data` and no return metric.

Each cell also reports `annualized_volatility_pct` and `sharpe_ratio_rf0`, both computed from per-bar returns with a zero risk-free rate and annualized at 252 sessions, or 252 × 390 minutes for Live Feed. Both need at least two returns, and both are unreliable on short runs. `turnover_ratio` is buys plus sells divided by average equity. `average_exposure_pct` is the mean share of equity held in positions rather than cash or dividend receivables.

Feed Batch analysis separates a historical backfill from a forward test. A session counts as `forward` only if its 16:00 New York close came after the cell's configuration was saved; early closes are not distinguished. Each cell reports `config_frozen_utc`, `backfill_sessions`, `forward_sessions`, and `forward_return_pct`, measured from the last backfill valuation. Each curve row and the CSV carry `phase`. Cells saved before this field use the experiment's creation time. Only the forward portion is evidence of how a frozen strategy behaves on data it could not have been tuned on.

These figures summarize simulated fills and saved valuations. They cannot correct omitted corporate actions or survivorship bias. The benchmark is another cell in the same experiment on the same dates, not an external market index.

## Local API server

```shell
python -m quantarium serve --workspace .
```

The app and its JSON API are served at `http://127.0.0.1:8765/`. The app is organized around the cell library:

- **Home:** add and configure a cell, then run it against a price file, a scenario suite, or a Feed Batch experiment. Manage can duplicate, archive, restore, or delete cells that have no deployments.
- **Cell page:** choose a saved result, inspect its holdings and timeline, and edit its latest configuration. The holdings view uses charts for up to 4 symbols, tiles for 5–30, and an aggregate view beyond that.
- **Feed and Static:** see forward-test and replay results as cell cards. Feed Batch experiments can be advanced or have a cell retired; Live Feed results are read-only in the app. A Live Feed control panel is deferred.
- **Scenarios, Log, Activity, Workspace:** create or transform scenarios, inspect runs and suites, follow queued jobs, and see discovered files and databases.
- **Lab:** register and run frozen experiments, read verdicts and caveats, and see every supercell's latest evidence.
- **Agents and Inbox:** turn agent features on or off, manage connections, review agent-written scripts, and approve or reject agents' proposals.
- **Learn:** replays the tutorial, and holds a 16-chapter handbook covering the UI, data and result meanings, the Lab, agents, supercells, and CLI-only features. The circled **?** beside each page title opens the handbook at a relevant chapter.

### First-run tutorial

The first time Quantarium opens a workspace, a title screen starts the tutorial (**Begin**, or **Skip tutorial**). It is a six-chapter quest through the real app, played in an empty Quantarium of its own:

1. **Build a market:** generate a fictional scenario in which one stock, AAA, rallies, crashes, and recovers.
2. **Hatch a cell:** add a cell, name it, give it AAA, and choose the TrendFollower script.
3. **Train:** test the cell on your market in Static, then read its results and its buy and sell markers.
4. **Enter the arena:** import the cell into a practice Feed experiment that starts partway through the market, then Advance it. A short briefing explains Live Feed, which runs from the command line.
5. **The record:** find the runs in Log and the jobs in Activity, then return Home.
6. **Test it honestly:** register and run a Lab experiment, read its controls and caveats, then repeat the same idea on the same market to see why reused data is exploratory.

Each chapter opens with a card listing its objectives. A spotlight highlights one control at a time; clicks elsewhere are blocked. An objective is checked off when the app shows you actually did it. **Do it for me** completes the current objective. Progress is saved in `.quantarium/onboarding.json`, so a reload resumes the same step. The tutorial is a state of the workspace. While it runs, a **Tutorial** badge shows in the top bar and every page shows only what the tutorial made; your own cells, scenarios, and records are hidden and untouched.

Finishing presents the cell you made and adds it to your workspace with its market, configs, Static result, and Feed practice, so its Feed experiment can still be advanced. The tutorial's Lab attempts remain private and are discarded. Skipping, or leaving partway, clears everything made in it and leaves your workspace exactly as it was. **Learn → Replay the tutorial** starts from empty again; a completed replay adds one more cell under names that don't collide with anything you have (`first_market_2`, `My first cell 2`, …). The tutorial never needs a data provider account.

When the tutorial finishes or is skipped, Quantarium copies its starter scripts (BuyAndHold, TrendFollower, EqualWeight, RecentLeader) to `strategies/starter.py` if that file doesn't exist; edit it or add your own files beside it. If you have edited that file, the tutorial uses its own copy, `strategies/starter_2.py`.

The default workspace scans `runs/` for databases, `configs/` for configs, `scenarios/` for scenario packages, `strategies/` and `configs/` for Python strategy classes, and `data/` for price files. A new workspace therefore starts empty. The repository's `examples/` folder serves the command-line examples; scripts in it still appear in the picker for cells that already use them. A `quantarium-workspace.json` file can override these folders. Cells are saved in `cells/*.json`; deployments write configs under `configs/generated/` and results under `runs/`.

Home lists saved library cells. Feed also displays saved experiments created directly from configuration files; their cells do not appear on Home unless separately saved in the library. The Feed page's **Import from Static** action selects a configured library cell and deploys it to Feed. A Static test is recommended before that step, but the app does not require one.

The server binds only to loopback, checks the `Host` header, and requires a per-session token for writes. Jobs run one at a time; running a selected strategy executes trusted local Python. See the [API reference](docs/api.md), [frontend specification](docs/frontend-spec.md), and [architecture decision](docs/adr/0011-local-api-and-frontend-architecture.md).

### Agent features (off by default)

Quantarium has an interface for AI agents ([plan](docs/plans/v0.3.0-agent-interface.md), [security review](docs/security-review-v0.5.md)). Every agent feature is off until you turn it on for a workspace. Open **Agents** in the app, read the notice, and type the confirmation shown, or run this in an interactive terminal:

```bash
py -m quantarium agents enable
```

`quantarium agents status` shows the setting and `quantarium agents disable` turns it off. The choice is stored in `.quantarium/agents.json`. If the notice changes in a later version, agent features turn off until you accept the new one. Once they are on, the Agents page creates **connections**: each has its own token and permissions (read, draft cells, run simulations, and, only if you allow it, manage cells and Feed). Quantarium checks the token, permissions, and the connection's budget on every request, whatever the model asks for, and records each request on the Agents page. **Pause all** stops every connection at once. An agent that also has its own shell or file access on your computer, such as Claude Code with its terminal tools, is bound by that tool's permission prompts as well, because Quantarium cannot limit what other programs do on your computer.

#### The built-in assistant

The **Assistant** button opens a chat docked beside every page, connected to your own model. It uses the same tools and limits as an external agent. Its replies can show live cell cards, jobs, and links to proposals waiting in the Inbox. It cannot approve anything.

There are two ways to connect it. Both end at the same checks.

**A model API.** Install the optional SDKs and create a **Model API** connection on the Agents page:

```bash
py -m pip install "quantarium[assistant]"
```

Pick a preset: Anthropic (Claude Opus 5.5 by default), OpenAI, OpenRouter, Groq, Mistral, DeepSeek, Google Gemini, xAI (Grok), OpenCode credits, or Ollama and LM Studio on your own computer. Or choose **Custom** for any endpoint that speaks OpenAI Chat Completions or the Anthropic Messages API. Then choose where the key lives:

- **An environment variable**, such as `ANTHROPIC_API_KEY`, set where you start `quantarium serve`.
- **The system keychain.** You paste the key once; Quantarium never saves it in a file.
- **No key**, for a server on your computer.

Keys go only to the connection's own endpoint, over HTTPS unless the server is on your computer, and never follow a redirect. **Test connection** checks that the model can call tools, which the assistant needs; **Models** lists what the endpoint offers. Usage is billed to that account, and the panel shows token counts. Presets are data in `quantarium/assets/agents/presets.json`, so adding a provider needs no code.

**An agent app** you installed and signed in to yourself. Quantarium runs it for each turn with only Quantarium's tools and a token that works for that turn alone, and never sees its sign-in:

- **Codex** with a ChatGPT plan. Codex's own shell, browser, and other tools are switched off, but its command runner can't be; if it starts a command, Quantarium stops the turn. Codex keeps its own transcript in `~/.codex/sessions`, and your `~/.codex/AGENTS.md` applies.
- **Claude Code** with a Claude plan or whatever else it is signed in with (experimental). Quantarium runs your unmodified `claude` with no built-in tools, only Quantarium's MCP server, and none of your user settings. Anthropic's terms allow signing in to the unmodified Claude Code with your own plan, but Anthropic has not confirmed this use for open-source apps. Creating the connection asks you to accept that the decision, and any action Anthropic takes on your account, is yours. Claude Code keeps its own transcript in `~/.claude/projects`, and your `~/.claude/CLAUDE.md` applies.
- **OpenCode** (experimental), with its free models, OpenCode credits, or the providers you connected in it. Quantarium switches OpenCode's own tools off for these turns and makes every other tool ask first. OpenCode's free models work only inside OpenCode, so use this rather than the OpenCode API preset for them.
- **Any Agent Client Protocol agent** (experimental), such as Gemini CLI or Goose. Quantarium offers it no file or terminal access, allows only Quantarium's tools when it asks permission, and stops the turn if it reports any other tool.

When you delete a chat in Quantarium you can delete the app's transcript too. For the strongest isolation, use a model API.

#### Connect an AI agent with MCP

`quantarium mcp` lets an MCP client such as Claude Code, Claude Desktop, or the Codex CLI use Quantarium. Its tools read the workspace, cells, scripts, experiments, result summaries, and jobs; create and edit cell drafts; write scripts; generate and transform scenarios; and run Static tests and suites. The connection's permissions decide which of these an agent may use. Until you read a script an agent wrote and mark it reviewed on the Agents page (or edit it yourself), it runs only in a sandbox during Static tests: a separate process with no access to your files, the network, other programs, or your keys. Feed never runs unreviewed agent code.

Agents cannot start Feed forward tests, Advance experiments, retire cells, or archive them. Instead they file **proposals**, which wait in the **Inbox** (the sidebar shows how many). Each shows the agent's reasoning, the exact action, the cells, and the scripts involved. Approve runs it as you; Reject can send a note back. If the cells or scripts change after the proposal was made, approving is refused. For a forward test, Approve unlocks only after you have read each agent-written script, because approving reviews them.

To give an agent's best strategy an honest check, open a scenario and choose **Hold out from agents**. Agents then cannot see that scenario, run on it, or read results from it, even from a copy. An agent may ask, through the Inbox, for one holdout test per cell version, and only you see the result. Summaries and proposals also report how many variants the agent has simulated, because the best of many tries flatters itself. Agents also wait while the tutorial runs. Advancing Feed and anything involving real money are not available to agents. It talks to the workspace's `quantarium serve`, starting one in the background if none is running. Create a connection on the Agents page, then give its token to the client as `QUANTARIUM_TOKEN`. Use your workspace's full path.

Claude Code:

```bash
claude mcp add quantarium --env QUANTARIUM_TOKEN=qtm_your_token -- py -m quantarium mcp --workspace C:\path\to\workspace
```

Claude Desktop (`claude_desktop_config.json`) or any client that takes the same shape:

```json
{"mcpServers": {"quantarium": {"command": "py", "args": ["-m", "quantarium", "mcp", "--workspace", "C:\\path\\to\\workspace"], "env": {"QUANTARIUM_TOKEN": "qtm_your_token"}}}}
```

Codex CLI (`~/.codex/config.toml`):

```toml
[mcp_servers.quantarium]
command = "py"
args = ["-m", "quantarium", "mcp", "--workspace", "C:\\path\\to\\workspace"]
env = { QUANTARIUM_TOKEN = "qtm_your_token" }
```

On macOS or Linux, use `python3` instead of `py`. The client keeps the token in its own settings file in plain text, so treat that file like a password. To cut off access, revoke the connection on the Agents page.

## View a local dashboard

Generate a self-contained HTML snapshot from saved Static, Feed Batch, or Live Feed results:

```shell
python -m quantarium dashboard live runs/live_replay_demo.sqlite --id live_replay_demo --benchmark live_single --output runs/live_replay_dashboard.html
python -m quantarium dashboard static runs/example.sqlite --output runs/static_dashboard.html
python -m quantarium dashboard feed runs/feed_demo.sqlite --id daily_demo --output runs/feed_dashboard.html
```

Open the output HTML file in a browser. The page compares indexed cell equity curves, reports return, drawdown, and orders, and lets you search and sort cells. It is read-only and works offline; rerun the command to include newer saved Feed bars. For a large experiment, the chart retains sampled highs and lows with a cap of 500 points per cell and about 12,000 total, while the metrics use every saved valuation. Use `analyze --csv` or `timeline` for full detail. The file embeds the displayed data, so treat it as a copy of experiment results. The [dashboard decision record](docs/adr/0003-offline-results-dashboard.md) describes the chosen format and alternatives.

## Inspect a cell's timeline

Read the saved backlog for one cell without rerunning its strategy:

```shell
python -m quantarium timeline static runs/example.sqlite single_stock --from 2024-01-03 --through 2024-01-05 --csv runs/single_stock_timeline.csv
python -m quantarium timeline feed runs/feed_demo.sqlite single_stock --id daily_demo --csv runs/feed_timeline.csv
```

The command groups each date's valuation, signal and target weights, orders, corporate actions, and skipped rebalance sessions. Static defaults to the latest run, including a failed run whose partial event trail may help diagnose a strategy; use `--id` to select another run. Feed requires `--id` with its `feed_id`. The CSV has one row per date and JSON columns for nested positions, decisions, and orders. Date filters apply before the CSV is written, so use them to inspect a short period in a long experiment.

## Strategy contract

The JSON config specifies each strategy as `module:Class`. Modules in the config's folder are importable without installing a package. The class constructor accepts optional `parameters`. Its `on_bar(context)` method is called after the current day's close.

`context` provides `date`, current `bars`, `history` through that date, `cash`, `positions`, and closing `equity`. In a changing universe, `bars` and `history` hold only the symbols the cell may hold after this close ([ADR-0039](docs/adr/0039-changing-universes.md)). A Simple strategy returns one target weight from `0` to `1` for its sole symbol. A Complex strategy returns a dictionary of symbols to target weights, whose sum is at most `1`. In a Complex cell, Quantarium multiplies those weights by the strategy's `allocation`, then adds all sleeves. Unallocated weight remains cash.

`context.cost_basis` gives the average price paid per share held, and `context.open_orders` shows resting orders. A one-strategy Static or Feed Batch cell can return `Decision(weights=..., orders=..., notes=...)` from `quantarium.model` instead of weights ([ADR-0025](docs/adr/0025-holdings-and-resting-orders.md)):

- `weights=None` keeps the current holdings without a rebalance at the next open.
- `orders` replaces all resting orders with a tuple of `Order(symbol, side, kind, shares, price, expires_after_sessions=None, tag="")`. Sides are `buy` or `sell`; kinds are `market`, `limit`, or `stop`. An empty tuple cancels all orders; `None` keeps them.
- Orders placed after a close first act on the next session. Stops pay slippage and can fill at a worse open after a gap; limits fill at their price or a better open. A daily bar cannot show intraday order, so a touched stop takes priority over a touched limit. A split adjusts resting shares and prices before fills.
- `notes` are saved with the signal. Live Feed does not accept `Decision` yet.

A cell can start with holdings, bought at the first session's open from `initial_cash`. The optional `cost` is the user's original per-share basis for strategy decisions:

```json
"initial_positions": {"NVDA": {"shares": 100, "cost": 120.0}}
```

An optional cell `information_end` ISO date records the last date whose outcome a model or rule could have used. Static and Feed Batch valuation events mark only later sessions as `post_information_end`. Typed by hand, the date is **self-declared and unverified**, and does not earn a trustworthy Lab verdict. A saved risk model card binds it to the card's last training label ([ADR-0030](docs/adr/0030-declared-information-boundaries.md), [ADR-0032](docs/adr/0032-data-only-risk-model-cards.md)).

See [examples/strategies.py](examples/strategies.py) and [examples/experiment.json](examples/experiment.json). User-supplied strategy scripts are trusted local Python code. Unreviewed agent-generated Static scripts run in Quantarium's sandbox, whose current protocol accepts weight returns only; review a script before using its `Decision` features.

For a single-file module in the config folder, Quantarium loads the exact bytes it hashes for experiment provenance. Reusing the same module name from another folder or editing the file in a long-running Python process creates a distinct loaded version. Imported helper modules and installed packages still follow Python's normal import cache; their transitive files are not included in the entrypoint hash. An unreviewed agent-written module is refused wherever it is imported, even from a reviewed script. Keep those dependencies fixed for a reproducible run, and use a fresh process when changing them. The [strategy loading decision record](docs/adr/0005-content-addressed-local-strategies.md) explains the choice and its limits.

## Portfolio controls

Each cell may set `rebalance_every_sessions` (positive integer, default `1`), `max_position_weight` (default `1`), `min_cash_weight` (default `0`), and `max_previous_volume_participation` (optional fraction from `0` to `1`). The rebalance interval counts processed market sessions from the first session: a value of `3` calls each strategy after sessions 1, 4, 7, and so on. Orders from each signal still fill at the next available open. On skipped sessions, Quantarium fills any prior pending order, values the portfolio, and records `rebalance_skipped`; it does not call the strategy or create a new target. The schedule resumes from the saved bar history in Feed batch.

When `max_previous_volume_participation` is set, each buy or sell fill is limited to `floor(previous session volume × fraction)` shares for that symbol. The prior session is already complete when the order executes, so this rule does not use the current day's eventual volume. Requested and filled shares, plus the computed cap, appear in each order event. Any unfilled amount expires; the next strategy decision may request it again. This is a conservative **liquidity proxy**, not a model of open-auction volume or price impact. The default is uncapped for compatibility with earlier runs.

The weight limits check the **combined target** after all Complex strategy sleeves contribute. For example, two equal-weight stocks with an `0.8` sleeve allocation target `0.4` each. A `max_position_weight` of `0.4` and `min_cash_weight` of `0.2` accept that target. A strategy target that violates either limit stops the run with an error; Quantarium does not silently change it. These are target limits at decision time, not a continuous cap on holdings after prices move. See [examples/policy_experiment.json](examples/policy_experiment.json).

## Current modeling boundaries

The tape must contain one valid daily OHLCV bar per requested symbol on every date in the run. Missing bars stop the run rather than silently inventing prices. Imported research data is the exception: there, symbols may list and delist during the run, but not skip a session in between ([ADR-0039](docs/adr/0039-changing-universes.md)). Cells share the tape but never cash or holdings. Orders generated by the last bar remain unexecuted, which is marked in the signal event. The CSV is loaded into memory; larger histories can therefore use substantial RAM.

Static replay handles only supplied splits and cash dividends. The optional Feed Batch action mode handles only supported US-dollar cash dividends and splits. These daily modes do not model intraday prices. Live Feed uses one-minute bars during one declared session and does not apply corporate actions. The project does not model taxes, withholding, reinvestment, fractional-share cash in lieu, due-bill processing, mergers, spin-offs, symbol changes, borrow/shorting, or real execution liquidity; delistings and index membership changes are modelled only from imported research data. Feed Batch requests **raw** bars, and even action-aware runs can lag provider publication. Cash is credited by the simulated payment session's close; actual brokerage credit can be later. Calendar validation identifies market sessions for Feed Batch, but it cannot explain why an individual stock lacks a bar. The supplied CSV is synthetic, so its returns say nothing about strategy quality. Historical research using real index universes needs point-in-time membership and delisted-stock data to avoid survivorship bias; Quantarium records whether your data claims to include them and says so in every verdict. Your own strategies run as trusted local Python; only unreviewed agent-written scripts run in the sandbox.

## Engine cross-check

Quantarium's engine is checked against [backtrader](https://www.backtrader.com/) 1.9.78.123, run in a separate environment that Quantarium never imports ([ADR-0038](docs/adr/0038-backtrader-as-reference-engine.md)). Under the same assumptions, both must produce the same fills and the same daily equity. All 68 cases (daily rebalancing, flat and per-share commissions with a minimum, rebalance intervals, volume caps, starting holdings, and resting stop and limit orders) match fill for fill:

```shell
py tools/crosscheck/run.py --reference-python path/to/reference-env/Scripts/python.exe
```

The reference environment holds only backtrader, installed as a wheel at that exact version. Setting `QUANTARIUM_REFERENCE_PYTHON` also runs the cross-check in the test suite.

## Security

To report a vulnerability, see [SECURITY.md](SECURITY.md). Quantarium's security rules for contributors and AI coding agents are in [AGENTS.md](AGENTS.md). Three reviews record the threat model, what was found and fixed, and the accepted risks: [v0.3.0](docs/security-review-v0.3.0.md), [v0.5](docs/security-review-v0.5.md), and [v1.0](docs/security-review-v1.0.md). The misuse tests in `tests/security/` run with the normal test suite; they plant fake secrets, stay on loopback, and check that Quantarium refuses misuse.

## After 1.0

[ADR-0037](docs/adr/0037-referee-not-competitor.md) sets the direction: Quantarium is a referee, not a competing engine. Next:

1. **Validation with real clients:** the new agent-app limits with Codex and OpenCode; `quantarium mcp` from Claude Desktop and the Codex CLI.
2. **Optional real-data paths:** validate Alpaca (`alpaca-check`, then Feed Batch); `import-bars` and the `csv` Feed provider remain the keyless real-data path. Exercise Live Feed during an actual session with a capture.
3. **Live Feed:** paper trading, under its own plan with every order confirmed by you and none placed by agents; reduce journal volume ([ADR-0007](docs/adr/0007-live-feed-quiet-minutes.md)); design gap recovery, cross-session continuity, and corporate actions.

The core uses Python's standard library, plus `tzdata` on Windows for New York time-zone rules. Live Feed alone needs the optional `websockets` package, and the built-in assistant the optional `quantarium[assistant]` extra (the Anthropic and OpenAI SDKs and `keyring`). The core can be benchmarked with realistic data before introducing pandas, NumPy, or a faster language.

## Scale probe

Run a synthetic portfolio benchmark on your machine:

```shell
python -m benchmarks.static_scale --symbols 500 --sessions 1000 --rebalance-every 21
```

The probe generates temporary weekday bars, runs one Complex equal-weight cell, reports generation and replay time, microseconds per bar, SQLite size, and the process's peak memory, then removes the temporary files. These are synthetic bars and weekdays, not market data or a trading-calendar test. `--trace-memory` adds Python's traced peak (`peak_traced_python_mib`, from `tracemalloc`), but slows the replay several times over, so its timings are not comparable. On the maintainer's Windows machine (Python 3.14), v0.8.3 replays 500 symbols over ten years (1.26 million bars) in about 7.4 seconds with a 425 MiB peak. The default probe uses 500 symbols and 252 sessions. Increase `--sessions` gradually to evaluate the memory cost of the in-memory tape before attempting a decades-long replay.

To measure memory used when many Feed Batch cells restore overlapping histories:

```shell
python -m benchmarks.feed_scale --symbols 100 --sessions 80 --cells 8
```

The Feed probe generates one synthetic market tape, runs all cells, then measures a restart that reloads their saved history. Compare different `--cells` values to estimate the incremental cost of cell state on your machine. Its traced Python peak excludes the synthetic provider's tape, which is allocated before tracing begins, and does not measure total process RAM.

To probe the synchronized Live Feed path with bounded minute history and overlapping cells:

```shell
python -m benchmarks.live_scale --symbols 100 --minutes 30 --cells 8 --history-limit 30
```

This probe generates synthetic bars one minute at a time, runs every cell, writes events to a temporary SQLite database, and reports runtime, database size, peak traced Python allocations, current and peak process resident memory, and private committed bytes where available. It does not open a WebSocket or include network latency. The resident-memory figure includes the interpreter and native SQLite working set; `tracemalloc` alone does not. In a Windows probe with 500 symbols and 60 minutes, moving from one to sixteen equal-weight cells changed peak resident memory from 49.52 to 56.87 MiB, runtime from 4.72 to 20.99 seconds, and SQLite size from 10.49 to 37.72 MiB. These are machine-specific synthetic results, and real Alpaca subscription limits may prevent the same symbol count on an account. The [Python-versus-C++ decision record](docs/adr/0004-python-for-minute-bar-live-feed.md) explains what this measurement supports and what remains to be tested.

## Upgrading from Barloom

Quantarium was called **Barloom** before 1.0. Everything Barloom wrote still works: an old workspace's `.barloom` folder becomes `.quantarium` the first time Quantarium opens it, and `barloom-workspace.json`, `BARLOOM_*` variables, `blm_` agent tokens, keys saved in the keychain, `barloom-*-v1` files, and scripts that import `barloom` are all read. `python -m barloom` still runs the command line.

## License

Quantarium is licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE.md): you may use, change, and share it for any noncommercial purpose, including personal study, research, hobby projects, teaching, and use by noncommercial organizations. Commercial use needs a separate license from the copyright holders (see [NOTICE](NOTICE)). Ideas and bug reports are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).
