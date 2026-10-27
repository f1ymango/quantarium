# ADR-0007: Quiet minutes in Live Feed

- **Status:** Accepted
- **Date:** 2026-10-20
- **Deciders:** Barloom maintainers; implementation choice recorded for user review
- **Amends:** [ADR-0001](0001-live-feed-timing-and-replay.md), which halted on any missing symbol-minute

## Context

Alpaca's stock stream publishes a minute bar only for a minute in which the symbol traded. On the IEX feed, which carries a small share of US volume, a minute with no trade is an ordinary market state for many symbols rather than a data fault. ADR-0001 treated every missing symbol-minute as missing data and halted the experiment. For a multi-symbol IEX subscription across a 390-minute session, that rule is expected to halt most runs early. This expectation has not yet been measured against a real session; `capture-quality` now reports the longest quiet run per symbol so the first real capture can confirm or refute it.

ADR-0001 rejected synthesizing bars or carrying prices forward because that invents a tradable minute. That concern still holds for execution. It does not require treating the last real trade as unknown for valuation.

Restoring a stopped experiment relied on every processed minute having a bar for every symbol: the last processed minute was taken from the newest saved bar.

## Decision

Add `max_quiet_minutes` to the Live Feed config, defaulting to `0`.

- At each minute's finalization deadline, a symbol without a bar is *quiet*. The experiment halts only when a symbol has been quiet for more than `max_quiet_minutes` consecutive minutes. A symbol never seen in the session counts from `start_at`. With `0`, behaviour is identical to ADR-0001, including the halt message.
- A quiet symbol keeps its last real bar, with that bar's own timestamp, in `context.bars`. It is listed in `context.quiet_symbols`, is valued at that bar's close, and gains no `history` entry for the quiet minute. No bar is synthesized or stored.
- Orders in a quiet symbol are journaled unfilled with `unfilled_reason: no_trade_in_interval`. A symbol never yet priced is absent from `context.bars`; a target for it is journaled with `requested_shares: null` and `unfilled_reason: no_price_observed`. Unfilled remainders are not carried forward, matching the existing treatment of cash- and volume-limited partial fills; the next signal decides again. Buys are still limited by available cash, so an unfilled sell cannot overdraw the account.
- A new `live_minutes` table records each processed minute, its bar count, and its finalization time. It is now the source of the last processed minute. On restart, the bar count for every processed minute must still match the saved bars. Databases created before this table existed are backfilled on open, since every minute in them was complete.
- A bar that arrives after its minute was finalized as quiet is logged in `live_revisions` with an empty `original_sha256` instead of raising. Before this change no such minute could be processed, so the path was unreachable.
- `live-report` adds `minutes_processed` and `quiet_symbol_minutes`. The live replay audit also compares `live_minutes` when both databases have it.

The default stays `0` because a configuration's meaning should not change when an experiment saved under an earlier version is resumed.

## Alternatives considered

| Choice | Benefit | Cost or reason not chosen |
| --- | --- | --- |
| Keep halting on every missing symbol-minute | Simplest; no stale prices | Likely unusable with IEX for more than a few liquid symbols |
| Synthesize a zero-volume bar at the last close | Strategies and execution see a complete grid | Invents a tradable open price and would fill orders in a minute with no trades |
| Carry unfilled quiet orders forward until the symbol trades | Closer to a resting market order | Adds a second pending-order state that interacts with superseding signals; deferred until real captures show quiet runs are long enough to matter |
| Subscribe to SIP instead of IEX | Far fewer quiet minutes | Requires a paid subscription; still does not remove quiet minutes for illiquid symbols |
| Halt when every symbol is quiet in the same minute, as a proxy for an outage | Detects a dead stream earlier | A single-symbol cell would halt on its first quiet minute; stream failure is already detected by the WebSocket connection and its keepalive |

## Consequences and follow-up

A strategy that assumed `context.bars` holds a bar for the current minute for every symbol must check `bar.timestamp` or `context.quiet_symbols` when `max_quiet_minutes` is positive. `EqualWeight` in the examples weights only symbols that have a price, so early in a session it may hold fewer names than configured.

The first real IEX capture should be inspected with `capture-quality` before choosing a value. If quiet runs are long and orders frequently go unfilled, revisit carry-forward orders.

### Performance note

While measuring this change, a profile of the synthetic 500-symbol, 16-cell probe showed that roughly 45% of runtime is JSON encoding of journal events and checkpoints. Each cell writes every position and price at every minute. Copying shared history per cell, which was suspected earlier, did not measurably contribute. History is now copied once per minute and shared by every cell, but the probe's runtime was unchanged (about 21 seconds before and after on the development machine). Any later throughput work should start with journal volume, for example by writing only changed positions, rather than with a language change.
