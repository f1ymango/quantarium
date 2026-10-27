# The referee: judging decisions made outside Quantarium

Quantarium can judge trading decisions made by another system: another backtesting framework, a language-model trading agent, a spreadsheet, or a person. It replays the decisions with its own fills, costs, and portfolio rules, and the Lab compares them with buy-and-hold and equal-weight controls. Other systems hand over **decisions, not returns**, so they cannot report their own fills. See the [plan](plans/v0.6.x-referee.md) and [ADR-0037](adr/0037-referee-not-competitor.md).

## Three ways in, and what each can prove

| Way in | How the decisions arrive | Highest Lab verdict |
| --- | --- | --- |
| **Decision file** | A finished file in the workspace's `decisions/` folder | `unknown`: a finished file may have been written with hindsight, and no one can tell from the file. Costs, controls, and drawdown are still shown |
| **Forward inbox** | Each session's decision is submitted before the next session opens, and Quantarium stamps its arrival | Every verdict, on scenarios of real prices covering the decisions' dates |
| **Decider** | Quantarium runs your program session by session and shows it only data up to each session | Every verdict, under the rules for model-driven cells: synthetic and disguised markets, and real history only after the program's training cutoff |

In every case a decision is made at the close of its date, using information through that close, and fills at the next open, as for Quantarium's own strategies.

## Decision files

JSON is the exact format:

```json
{
  "format": "quantarium-decisions-v1",
  "source": {"system": "my-agent", "version": "0.3", "model": "example-model", "training_cutoff": "2025-01-31"},
  "symbols": ["AAA", "BBB"],
  "decisions": [
    {"date": "2030-01-07", "weights": {"AAA": 0.5, "BBB": 0.3}, "note": "trend up"},
    {"date": "2030-01-08", "weights": null},
    {"date": "2030-01-09", "weights": {"AAA": 0.2},
     "orders": [{"symbol": "BBB", "side": "buy", "kind": "limit", "shares": 10, "price": 9.5}]}
  ]
}
```

- `weights` are target shares of the account from 0 to 1, totalling at most 1; the rest is cash. `null` keeps the current holdings, and so does a date with no entry.
- `orders` are optional resting orders (`market`, `limit`, or `stop`), as in a script's `Decision`.
- `source` is shown in reports and is not verified.

A CSV with the columns `date,symbol,weight` carries weights only, one row per symbol and date. `examples/decisions/steady_bull_weekly.csv` is an example. Check a file with:

```shell
python -m quantarium decisions check decisions/my_decisions.csv
```

### Exporting from other systems

Any system that knows its target weights each day can write the CSV. For example, with pandas, from a DataFrame of target weights indexed by date with one column per symbol:

```python
weights.stack().rename("weight").rename_axis(["date", "symbol"]).reset_index().to_csv("decisions/mine.csv", index=False)
```

In an event-driven framework, record the target weights your strategy computes at each close, and write them out at the end in the same three columns. Export decisions, not fills or returns: Quantarium computes those itself.

### Your own trades

A broker's trade history becomes a decision file with `decisions from-trades`. The CSV needs `date,symbol,side,shares,price`, one row per fill; other columns are ignored, and fractional shares are refused. A fill on a session becomes a market order decided at the previous close, on the NYSE calendar or, with `--prices`, on a price file's sessions. Same-day trades in one symbol are netted, so intraday round trips drop out and are listed. The report suggests starting holdings when shares are sold before they are bought, and lists the limits: deposits and withdrawals, dividends and fees outside fills, and short sales are not modelled.

After replaying the file, `decisions gap` compares each of Quantarium's fills with the real average price for that day and symbol. A positive gap means Quantarium's fill was worse for you; the summary gives the mean gap in basis points, weighted by value, and the total in dollars.

## Forward inboxes

Open an inbox for some symbols, then submit each session's decision before the next session opens (09:30 New York):

```shell
python -m quantarium decisions inbox-create my_agent --symbols AAA,BBB
python -m quantarium decisions submit my_agent --date 2030-01-07 --weights AAA=0.5,BBB=0.3
python -m quantarium decisions submit my_agent --date 2030-01-08 --hold
```

An agent connected through `quantarium mcp` can do the same with `create_decision_inbox` and `submit_decision`, with the simulate permission and a budget of 500 actions a day. A program can call the API's `decision-submit` command. A later on-time decision for the same date replaces the earlier one; a late one is kept on record and left out. Choose the inbox on a cell's Configure tab; Feed Batch follows it forward.

## Deciders

A decider is a program you register. Quantarium starts it for each run in an empty folder, with only basic system variables and the environment variables you name, and talks to it in JSON lines on its standard input and output:

1. Quantarium sends `{"op": "init", "protocol": 1, "symbols": [...]}`; the decider answers `{"ok": true}`.
2. For each session, Quantarium sends `{"op": "session", "date", "bars", "history", "cash", "positions", "equity"}`. Bars are `[date, open, high, low, close, volume]`. `history` holds only the bars not sent before, under `append`, or all of them with `"reset": true` after a split.
3. The decider answers `{"weights": {...} or null, "orders": [...], "note": "..."}` within 120 seconds.

`examples/deciders/moving_average.py` is a complete decider in the standard library. Register one:

```shell
python -m quantarium decisions decider-create moving_average -- python examples/deciders/moving_average.py 20
```

or with **Register a decider** under Outside decisions on a cell's Configure tab. Before the `--`, add `--env MY_MODEL_KEY` for a key the program needs, and `--training-cutoff 2025-01-31` with the last date its model was trained on; everything after the `--` is the program's own command line.

Only you can register, choose, or run a decider. An agent cannot, because a decider is your program and may spend your money. Quantarium shows a decider only data up to each session but cannot stop it from reading other sources, so on real history the Lab judges it like a language model: only after its training cutoff. Every run is recorded in `decisions/recorded/`, and a recording reads as a decision file, so a run can be rescored without running the program again.
