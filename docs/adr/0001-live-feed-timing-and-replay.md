# ADR-0001: Live Feed timing, shared input, and offline replay

- **Status:** Accepted for the experimental single-session Live Feed
- **Date:** 2026-10-20
- **Deciders:** Barloom maintainers; implementation choice recorded for user review

## Context

Barloom already runs multiple independent portfolio cells over one market tape. Live Feed must keep their accounts separate while consuming real-time data on a laptop. The user selected completed one-minute bars for initial strategy decisions. Alpaca's stock stream can publish updates to a minute bar after its first transmission. During development, credentials and an open market session are unavailable, so the same strategy path also needs a reproducible offline exercise.

## Decision

Use one Python process and one Alpaca WebSocket subscription for the union of cell symbols. Fan each finalized minute out to all configured cells. Keep one bounded minute history per symbol, shared read-only by the cells; save bars, per-cell checkpoints, decisions, and fills in SQLite.

Wait 95 seconds from each minute's start by default before making a decision. Accept an updated bar up to that point. A decision may fill only at the open of a later minute that starts after the decision was available. A revision received after finalization is logged without rewriting decisions. Halt when a required symbol's bar is missing or a minute cannot be finalized in time for a live decision. The initial run covers one declared regular New York trading session.

Support `live-replay` with timestamped incoming minute frames. A manually prepared replay advances the same engine's clock at the finalization deadlines. An optional live capture records post-subscription frames and the local clock ticks used by the original run; replay of this format uses those recorded ticks, including an incomplete capture's stopping point. Save `input_kind=replay` and the input file's SHA-256 beside the experiment; a changed file cannot resume the same `feed_id`. A WebSocket run saves `input_kind=websocket`. Replay of synthetic frames is a behavioral test, not evidence of live Alpaca access or execution quality.

## Alternatives considered

| Choice | Benefit | Cost or reason deferred |
| --- | --- | --- |
| Stream trades or quotes and construct bars locally | More control over event timing and possible subminute strategies | Much larger event volume and additional market microstructure rules before the first useful version |
| One socket and full tape per cell | Straightforward cell isolation | Duplicates subscriptions and history as cell count grows |
| Act on the first minute-bar message | Faster simulated decision | A later update may change the bar used for that decision |
| Interpolate missing bars or carry forward prices | Keeps an experiment running through sparse data | Invents a tradable minute and hides data-quality failures |
| Replay the capture through Static | Reuses an existing command | Static is daily and cannot verify Live Feed timing, updates, or later-open fills |
| Build the first Live Feed in C++ | Potential lower per-event overhead | Adds implementation cost before measured Python minute-bar processing indicates a need |

## Consequences and follow-up

The 95-second delay means the earliest simulated fill for a 09:30 minute signal is the 09:32 minute open. The current fill model uses that later bar's open once the bar arrives; it does not represent a broker order or guarantee such a price. A missing bar, stream disconnect, or account subscription limit may stop the run. The bounded history limits Python memory growth, while SQLite grows with every retained bar and cell event. One process still calls every cell's strategy serially; a slow strategy can delay all cells and trigger a halt.

Test the WebSocket adapter against a real Alpaca session and measure process RSS, CPU, and database growth on the intended laptop. Decide whether to add gap backfill, cross-session state and corporate actions only after that evidence. Keep Feed Batch as the multi-day forward-testing path in the meantime.
