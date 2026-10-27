# ADR-0037: A referee, not a competing engine

- **Status:** Accepted for the roadmap to v1.0
- **Date:** 2026-10-24
- **Deciders:** Barloom maintainers

## Context

Barloom began as a local simulator for stock portfolio strategies. By v0.5.5 it had three layers: a cell-based simulator, a Lab that judges whether a result is evidence (pre-registration, controls, used data, holdouts, variant counts, training cutoffs, disguised replays, ablations), and a contained harness for AI agents.

Established backtesting engines (Lean, NautilusTrader, vectorbt, zipline-reloaded, backtrader) are far ahead on speed, asset classes, order types, and broker connections. Projects that let language models trade (TradingAgents, ai-hedge-fund, FinRL) produce decisions, but rarely judge them carefully. Few projects combine an honest evaluation with safe use of AI agents.

Two ways forward were considered for the engine:

1. **Compete:** rebuild Barloom's engine to match established engines in breadth and speed.
2. **Incorporate:** run an established engine inside Barloom.

Competing would take years of work in the most crowded part of the field. Incorporating would bring heavy dependencies into a standard-library core, licenses that range from Apache-2.0 to GPL and AGPL, and other projects' code into Barloom's process, against the provenance and sandbox rules of v0.5.

## Decision

**Barloom is a referee.** Its purpose is to say honestly whether a trading idea, the user's or an AI agent's, holds up within a stated scope. Its engine is kept, and strengthened only where that affects the evidence.

- **Decisions, not returns.** Barloom judges other systems by replaying their decisions (target weights or orders, dated) on its own engine, with its own costs, controls, and Lab rules. A system cannot report its own fills or returns to the Lab.
- **No other engine in Barloom's process or dependencies.** An established engine may be used to check Barloom's, in its own environment, exchanging only files. Barloom's core stays standard-library only.
- **Agree where assumptions match; explain where they differ.** The engine is cross-checked against an established engine on shared data. A difference under the same assumptions is a bug to fix. A difference from a different assumption, such as close fills against next-open fills, is documented, not tuned away.
- **Strengthen for evidence first.** Engine work goes first to what can change a verdict: costs and slippage, handling point-in-time universes and delisted securities in the user's own data, and memory for long histories. Speed and breadth come later, if at all.
- **Simple by default.** The defaults and guardrails stand in for expertise the user may lack. Each result states its scope and limits plainly.

## Consequences

- The roadmap to v1.0 (`docs/plans/roadmap-to-v1.md`) is ordered as referee, evidence, a more accurate engine, compatibility with the user's own data, then public release.
- Barloom supplies compatibility, not market data. Users bring their own data and are responsible for its quality.
- Some users will need what Barloom does not model: shorting, intraday execution, other asset classes. Barloom states this and does not try to cover it.
- A verdict from replayed decisions is weaker than one from a strategy Barloom can rerun, because fixed decisions cannot be rerun on a disguised market or without their model. The Lab must show the difference.
- The cross-check needs an established engine installed in a separate environment, with the maintainer's approval under `AGENTS.md`.
