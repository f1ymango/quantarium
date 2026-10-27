# ADR-0008: Split-adjusted strategy history

- **Status:** Accepted
- **Date:** 2026-10-20
- **Deciders:** Barloom maintainers; implementation choice recorded for user review

## Context

Static and Feed Batch account for splits and dividends on **raw, unadjusted** bars, because adjusted bars would double-count actions in the account. Strategies received those same raw bars in `context.history`. After a 2-for-1 split, a strategy comparing today's close with yesterday's saw a 50% drop that never happened to an investor. A test strategy that exits on a 25% one-day drop sold its entire position on the split day. The previous-volume fill cap had the matching error: on a split day it compared today's post-split share counts with yesterday's pre-split volume.

## Decision

Keep the stored tape, journal, and account on raw prices. Give each cell runtime a per-symbol list of splits it has applied, recorded when the split is processed and therefore never ahead of the simulated date. `BarHistory` divides the prices of bars dated before each applied split by the split ratio and multiplies their volume. The current bar is unchanged. With no splits, the view returns the original bar objects at no extra cost.

The previous-volume participation cap reads prior volume through the same adjusted view.

Applied splits are saved in the cell checkpoint under `splits`, only when a cell has one, so Feed Batch restarts show the same history. A checkpoint written before this change has no `splits` key; splits it already applied stay unadjusted in the strategy view, while later splits are adjusted. Such an experiment should be restarted under a new `feed_id` if its strategies read prices across an earlier split.

Live Feed does not model corporate actions and is unaffected.

## Alternatives considered

| Choice | Benefit | Cost or reason not chosen |
| --- | --- | --- |
| Leave strategies on raw bars and document it | No change | Every strategy author must reimplement adjustment, and the error is easy to miss |
| Feed adjusted bars to both account and strategies | One price series | Double-counts actions in the account, the reason raw bars were required |
| Also adjust for dividends (total-return view) | Return-based signals include income | Changes price levels that strategies may compare with the current raw bar; can be added later as an opt-in view |
| Rewrite stored history when a split arrives | Simple reads | Mutates shared Feed Batch history and loses the first-seen record the audits rely on |
| Rebuild applied splits from journal events on restart | Fixes old Feed checkpoints too | More coupling between checkpoint restore and journal format for a transitional case |

## Consequences

Strategy outputs around a split change compared with earlier versions of Barloom, which is the intent. Static and Feed Batch use the same runtime, so the Static-versus-Feed replay audit remains comparable. A strategy that needs raw historical prices has no direct accessor yet.
