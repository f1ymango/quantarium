# ADR-0002: Isolate Live Feed strategy failures by cell

- **Status:** Accepted for Live Feed
- **Date:** 2026-10-20
- **Deciders:** Quantarium maintainers; implementation choice recorded for user review

## Context

Cells represent independent simulated accounts that share market input. The first Live Feed implementation committed a complete minute across every cell in one SQLite transaction. If one strategy raised an exception, every cell lost that minute and the live experiment stopped. A user testing several scripts at once should be able to retain results from healthy cells while still seeing exactly where a faulty script stopped.

## Decision

Keep market-bar ingestion atomic for the experiment. Within each market minute, process each active cell under its own SQLite savepoint. If its strategy, target validation, or checkpoint serialization raises an ordinary exception, roll back that cell's events and checkpoint changes for the minute, mark the cell `failed`, and record a `cell_failed` event with the exception type and message. Do not invoke that cell again. Other cells continue on the same saved bars, including after a process restart. If no active cells remain, halt the experiment.

Market-data gaps, invalid bars, SQLite failures, and concurrent-runner conflicts remain experiment-level failures. A failed cell's prior account checkpoint stays readable. Live analysis aligns benchmark curves by actual UTC minute and leaves comparisons blank where one cell has no valuation for that minute. The timeline CSV includes the failure event.

## Alternatives considered

| Choice | Benefit | Cost or reason not chosen |
| --- | --- | --- |
| Stop the entire experiment on any strategy exception | Simple, one common endpoint for every cell | A single experimental script destroys the forward test of unrelated cells |
| Run every cell in a separate OS process and database | Stronger fault and resource isolation | Duplicates shared feed management and increases laptop memory and coordination cost before it is measured to be necessary |
| Catch errors and keep calling the failed strategy on later minutes | Could recover from a transient script error | Its internal state may have changed during the exception and cannot be trusted without a rollback contract |

## Consequences and follow-up

Cells can have different final timestamps. Comparisons between cells are meaningful only over minutes where both have valuations; aggregate excess return is withheld when their endpoints differ. A strategy that mutates external state before throwing cannot have that external effect rolled back. Strategy code remains trusted local Python and runs serially inside the process, so an infinite loop still stalls all cells; separate process isolation may be revisited if real usage requires it. [ADR-0009](0009-cell-lifecycle-across-modes.md) later applied the same isolation to Static and Feed Batch.
