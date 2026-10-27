# ADR-0039: Changing universes in Static runs

- **Status:** Accepted for v0.9.1
- **Date:** 2026-10-26
- **Deciders:** Quantarium maintainers

## Context

Until v0.9, a Static tape needed a bar for every symbol on every session. Research data that is free of survivorship bias, which v0.9.0 lets users import, breaks that rule on purpose: companies list after the start, go bankrupt, are taken over, and enter and leave indexes. Refusing such data keeps users on survivors-only data, which flatters every strategy. Accepting it means deciding what the engine does when a symbol appears, disappears, or leaves an index.

## Decision

A run may declare a **changing universe**. Imported research scenarios always do; a Static config does so with `"changing_universe": true`, or by naming `delistings_path` or `membership_path`. Without it, nothing changes: every symbol must trade every session, and the engine takes exactly its old path.

In a changing universe:

1. **Sessions** come from the whole price file, not only the run's symbols. A cell whose symbols all delist still reaches the session that pays them out.
2. **A symbol trades** from its first bar to its last. A gap in between is refused; trading halts are not modelled.
3. **What a strategy sees** at a close is `context.bars` and `context.history` for the symbols it may hold next: those trading that session and, for a cell with `index`, members of that index on that date. Existing strategies, which iterate `context.bars`, need no change. When nothing qualifies, the strategy is not called and the cell holds cash.
4. **Weight on a symbol outside that set** stays in cash and is listed in the signal's `outside_universe`, as a broker would refuse the order. It is not an error, so a one-symbol cell survives its stock's delisting. Buy orders outside the set are rejected with an `order_rejected` event; sell orders on a held symbol that still trades are allowed.
5. **A delisting** happens at the first session on which a held symbol has no bar. The holding is paid out at the delisting file's price, or at the last close when no price is given, with no slippage or commission, and recorded as a `delisted` event; its resting orders are cancelled. A symbol that stops without a delisting row is paid at its last close (`prices_stopped`), and `data check` warns about it.
6. **Starting holdings** must trade on the first session.
7. **Volume caps** apply as before; a symbol without a previous session has no volume, so a cap gives it no fill.

Feed Batch and Live Feed still replay a fixed set of symbols and refuse index cells.

## Alternatives considered

- **Refuse weights outside the universe as an error.** Stricter, but a buy-and-hold cell would fail on its stock's delisting, and the engine already treats an unpriced symbol as an unfilled order, not a failure. The `outside_universe` record keeps the event visible.
- **Close delisted holdings at zero.** Too harsh for takeovers and too generous to data that omits recoveries; the user's delisting file says what holders received.
- **Show strategies every symbol with a flag.** Strategies would need to learn the flag; restricting `context.bars` keeps them correct without changes.

## Consequences

- A fixed universe replays event for event as before; `tests/test_universe.py` checks this on every example scenario and strategy, so the v0.8 cross-check against backtrader still covers it.
- backtrader has no model of delisting payouts or index membership, so these rules are checked by Quantarium's own tests on the invented example index, not by the cross-check.
- A changing-universe run loads every symbol in its price file, to know the calendar; very large files use more memory than a run of a few of their symbols would.
- The Lab's disguised replay does not yet copy delisting and membership files, so it skips imported scenarios with a reason.
