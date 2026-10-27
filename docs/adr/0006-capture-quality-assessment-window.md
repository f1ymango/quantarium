# ADR-0006: Assess Live capture quality only through reached deadlines

- **Status:** Accepted for capture diagnostics
- **Date:** 2026-10-20
- **Deciders:** Quantarium maintainers; implementation choice recorded for user review

## Context

A Live Feed capture may end early after a disconnect, an interrupted process, or a deliberate short run. Reporting every later minute in the declared session as missing would confuse an incomplete observation window with poor market coverage. Initial minute bars and updates also arrive at different times, and a recorded frame can itself trigger a finalization clock flush.

## Decision

Add a read-only `capture-quality` report. For a capture produced by `--capture`, assess a minute only after a recorded frame or clock tick reaches its finalization deadline. Treat bars and updates in that same frame as received before the flush, matching the Live Feed runner. For a manually prepared headerless replay file, use ideal finalization deadlines across the declared interval, matching its replay semantics. Report assessed versus declared minutes, on-time symbol-minutes, missing and late-only symbol-minutes, updates before and after finalization, unexpected symbols, stream errors, and a bounded list of issues. Do not write an experiment or synthesize prices.

## Alternatives considered

| Choice | Benefit | Cost or reason not chosen |
| --- | --- | --- |
| Count every declared minute after an early capture end as missing | Simple total coverage percentage | Mislabels unobserved future time as a provider gap |
| Assume a missing bar is a zero-volume bar with the prior close | Keeps a strategy running | Invents market data and a possible fill price |
| Run full strategy replay just to diagnose the tape | Reuses the minute engine | Creates account records and can fail on strategy logic before revealing basic feed coverage |

## Consequences and follow-up

Coverage is conditional on the assessed window. A short capture can show full coverage of one minute while saying little about the rest of the session. The report uses the capture's receive timestamps; it cannot determine whether Alpaca omitted trades, whether a plan had sufficient symbol entitlements, or whether the user's historical universe includes delisted stocks. Use the result to select suitable Live Feed symbols or investigate a stopped session before interpreting strategy performance.
