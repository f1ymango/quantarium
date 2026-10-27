# ADR-0010: Scenario inputs and scenario suites

- **Status:** Accepted and implemented
- **Date:** 2026-10-20
- **Deciders:** Quantarium maintainers; plan recorded for user review

## Context

The user wants to judge one algorithm under different market conditions, such as a recession, a long rise, or a sudden crash, and compare the results side by side. Input must stay in formats Quantarium defines, so customization cannot break the engine.

What exists today:

- Static accepts any daily OHLCV CSV in its documented format, plus an optional action CSV. Running the same cells against a different file already works.
- Live replay accepts any timestamped JSONL of Alpaca-style minute frames.
- Nothing names or describes an input, records how it was produced, produces scenario data, runs one set of cells across several inputs, or compares results across runs. Cells are tied to fixed ticker symbols, so a synthetic or historical tape must use the same symbols.

Static and Feed Batch produce identical journals from identical bars (the replay audit checks this). Feeding a scenario into Feed Batch would therefore test its incremental mechanics, not the strategy. A forward test is defined by real data arriving after the strategy was frozen, so scenario results must never be presented as forward evidence.

## Decision

Introduce two concepts on top of the existing Static input format.

### Scenario: a named, described input package

A directory with a manifest and files in formats Quantarium already validates:

```
scenarios/covid_crash_2020/
  scenario.json      name, description, kind, tags, provenance, symbols, hashes
  prices.csv         the existing Static OHLCV format
  actions.csv        optional, the existing action format
```

`kind` is one of:

| Kind | Produced by | Example |
| --- | --- | --- |
| `historical` | Slicing real saved bars, such as a Feed Batch backfill export, by date range | 2020-02-01 to 2020-04-30; 2022 bear market |
| `synthetic` | A seeded generator; the same parameters and seed always produce the same file | A two-year rise, a sideways market, a regime switch, a crash |
| `transformed` | A recorded transformation of another scenario, keeping the parent's hash | A real 2023 tape with a 35% five-day crash inserted, or doubled volatility |

`tags` are free-form labels such as `recession`, `crash`, `bull`, or `high_volatility`, used to group results. Quantarium does not infer them.

Generators and transforms work on the daily tape only and produce valid OHLCV bars (`low ≤ open, close ≤ high`, positive prices, integer volume), so the existing loader and engine are unchanged. The first generators are deliberately simple and documented:

- geometric random walk with per-segment drift and volatility, and a shared market factor so symbols move together;
- a shock transform that applies a chosen drawdown over N sessions from a date, optionally followed by recovery;
- volatility and drift scaling of an existing tape.

### Suite: one set of cells across many scenarios

```
suite.json
  cells:      defined once, as in a Static config
  scenarios:  list of scenario directories, each with an optional symbol_map
  costs:      slippage_bps, commission_per_order
  database_path
```

`symbol_map` renames a scenario's symbols to the cells' symbols, for example synthetic `S1` to `AAPL`, so one cell definition runs on any scenario. Every scenario must provide every symbol the cells use after mapping, or the suite refuses to start.

`quantarium suite run` executes one Static run per scenario into a single database. Each run is tagged with the scenario's name, kind, tags, and manifest hash. Cell failure isolation from ADR-0009 applies per run.

`quantarium suite report` builds a cells-by-scenarios matrix of total return, maximum drawdown, volatility, turnover, and excess return against a chosen benchmark cell, plus averages per tag. It also exports the matrix as CSV. This adds the cross-run comparison and the missing risk metrics from the gap list.

Every suite report shows each scenario's `kind`. Results from `synthetic` and `transformed` scenarios are labeled as such. `historical` scenarios carry the survivorship-bias warning when their symbols were chosen from today's lists.

### Other modes

- **Feed Batch** gains a file-backed provider that releases a scenario tape one session per call. Its purpose is to test resume, checkpoint, and stateful-strategy behaviour on chosen conditions. Such experiments record `input_kind: scenario` and are excluded from anything labeled forward-test.
- **Live Feed** already replays custom JSONL. Minute-level scenario generation waits for Static minute-bar replay, which is still on the roadmap.

### Implementation order

1. Scenario manifest, loader, and validation; a `historical` slice from an existing CSV or Feed export.
2. Suite config, `suite run`, and `suite report` with the metrics above.
3. Synthetic generator and shock, volatility, and drift transforms.
4. File-backed Feed Batch provider.

## Alternatives considered

| Choice | Benefit | Cost or reason not chosen |
| --- | --- | --- |
| Rely on separate Static configs pointing at different CSVs | Works today | No provenance, no tags, no comparison; easy to compare runs with different cells or costs by mistake |
| Make cells symbol-agnostic (roles such as "asset 1") | Any scenario fits any cell | Changes every strategy's contract; `symbol_map` gives the same reuse without that |
| Automatically classify historical periods as recession or bull | Less manual tagging | Classification rules are themselves a research choice; explicit tags keep it visible |
| Bootstrap or resample real returns as the first generator | More realistic distributions | Harder to explain and verify; can follow the simple generators |
| Run scenarios through Feed Batch for "realism" | Exercises incremental code | Produces the same results as Static more slowly, and risks labeling scenario output as a forward test |
| Fetch historical windows directly from Alpaca inside `suite run` | One step | Network access inside a research run breaks reproducibility; slicing a saved export pins the bars |

## Consequences

Scenario packages make the inputs behind a comparison explicit and hash-pinned. Synthetic markets cannot show how a strategy would have behaved in a real recession; at most they show sensitivity to the shape of a market the user specified. Realistic historical regimes still depend on available data: Alpaca stock history starts in 2016, so 2008 is not available from that source. Delisted symbols and point-in-time index membership remain open problems. This plan also partly addresses the cell-identity drift noted in review: a suite defines cells once and reuses them across inputs.
