# ADR-0009: Cell failure, retirement, and late additions across modes

- **Status:** Accepted
- **Date:** 2026-10-20
- **Deciders:** Barloom maintainers; implementation choice recorded for user review
- **Extends:** [ADR-0002](0002-live-cell-failure-isolation.md), which isolated failures for Live Feed only

## Context

Cells are independent accounts, but before this change:

- In Static and Feed Batch, one strategy exception failed the whole run or day for every cell. A portfolio limit violation (`max_position_weight`, `min_cash_weight`) or an unmodeled fractional split did the same.
- The only way to stop one cell was to stop the experiment. Feed Batch pinned the hash of the entire config, so a new cell meant a new `feed_id` and the loss of the running forward test's continuity.
- Feed Batch fetched every configured symbol, so one delisted symbol held by an abandoned cell would halt every other cell.

## Decision

**Failure isolation everywhere.** Static and Feed Batch adopt ADR-0002's rule. An ordinary exception while a cell processes a session rolls back that cell's partial session. The cell is marked `failed` with the error, a `cell_failed` event is recorded, and the cell is never called again. SQLite errors and market-data problems remain experiment-level. Strategy load errors also remain experiment-level, because they are configuration mistakes to fix before running. In Static, the run still finishes `completed`, and each cell carries `status` and `error`. A failed Static cell's `final_equity` is its last committed valuation. Since Static and Feed Batch share `CellRuntime`, a deterministic failure produces identical journals, and `audit-replay` still matches.

**Retirement.** `retire-cell {feed|live}` marks an active cell `retired` and records a `cell_retired` event, with an optional reason, at the last processed session or minute. It cannot be undone. Both runners re-read event counters and cell statuses inside their write transaction, so retiring a Live Feed cell while the experiment is running is safe; the runner skips the cell from its next minute. A Live Feed experiment with no active cells halts.

**Fetch only what active cells need.** Feed Batch requests symbols for active cells only. A retired or failed cell's symbols stop accumulating bars.

**Adding Feed Batch cells.** Feed Batch now pins two kinds of hash: the config without its `cells` list (experiment settings), and each cell's parsed specification. On resume:

- changed settings, a changed existing cell, or a removed cell is refused (use `retire-cell` instead of removing);
- a new cell name is added with `joined_after` set to the last processed session. Its checkpoint starts there, and it first trades on the next session. It sees the experiment's saved history for its symbols as point-in-time past data.
- A new cell may use a symbol only if that symbol's saved history is complete or absent. A gap left by a retired cell would otherwise give a strategy an irregular history.

An experiment created before this change resumes only with its exact original config. That run records the new hashes, after which cells can be added. The last processed session is now the newest saved bar date, and every active cell's checkpoint must match it.

**Analysis.** Curves may have different spans in every mode. Aggregate excess return is reported only when a cell and the benchmark start and end at the same time. For a cell that joined late, the per-row benchmark index is rebased to the cell's index at its first valuation.

## Alternatives considered

| Choice | Benefit | Cost or reason not chosen |
| --- | --- | --- |
| Keep whole-run failure in daily modes | Every cell always shares one endpoint | One faulty script or one limit breach erases unrelated results |
| Record a limit violation as a rejected proposal and keep the cell running | Closer to the blueprint's "rejected proposals" | Needs a defined fallback target (hold, cash, or clip); left as its own decision |
| Allow editing an existing cell in place | Convenient | Its earlier results would no longer describe the configuration on record |
| Allow removing a cell from the config | Simpler config files | Silently drops a recorded account; retirement keeps it visible |
| Let a late cell start on day one by replaying saved bars | Same span as other cells | Not a forward test: the strategy would be chosen with knowledge of those sessions |
| Keep fetching retired cells' symbols | Complete history for any future cell | A delisted symbol nobody trades could halt the experiment |

## Consequences

Comparisons across cells need care because spans differ; reports show each cell's status. A Static replay of a Feed export cannot reproduce a cell that joined late or was retired, so `audit-replay` reports those cells as mismatches, which is correct. Retired cells' symbols can leave gaps that block later cells from using them in the same experiment.
