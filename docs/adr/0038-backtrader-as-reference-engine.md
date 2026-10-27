# ADR-0038: backtrader as the reference engine

- **Status:** Accepted for v0.8
- **Date:** 2026-10-26
- **Deciders:** Barloom maintainers

## Context

ADR-0037 makes Barloom's engine more accurate by cross-checking it against an established engine, run in its own environment. The roadmap named zipline-reloaded (3.2.0). Resolving it on the maintainer's Windows computer showed that its storage library, `bcolz-zipline`, publishes Windows wheels only up to Python 3.12, while the computer has Python 3.13 and 3.14. The rest of its roughly 65 dependencies also included releases younger than seven days, which `AGENTS.md` asks to avoid. The alternatives were to install Python 3.12, to compile `bcolz-zipline` from source (running its build script during installation, with C++ build tools), or to choose another engine.

## Decision

**Use backtrader 1.9.78.123** as the reference engine.

- It is a single pure-Python wheel with no required dependencies, released in 2023, so nothing is compiled and nothing new is pulled in.
- It is event-driven like Barloom, and its `cheat_on_open` mode lets a strategy size orders at the next bar's open, which is how Barloom rebalances.
- Its licence (GPL-3.0 or later) does not affect Barloom: Barloom never imports, links, or ships it. It runs in a separate environment and exchanges files.
- It is no longer maintained. As a fixed ruler, that is acceptable.

## Consequences

- The cross-check lives in `tools/crosscheck/`, outside the `barloom` package. Its runner runs in the reference environment; its driver runs in Barloom's.
- backtrader's own semantics (order types, cash checks, rounding) must be set to Barloom's assumptions case by case; the runner documents each setting.
- A second ruler (zipline-reloaded on Python 3.12, or Lean) remains possible later.
