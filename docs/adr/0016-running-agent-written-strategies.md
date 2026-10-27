# ADR-0016: Running agent-written strategies

- **Status:** Accepted (Parts 6, 9, and 10 of the [v0.3.0 plan](../plans/v0.3.0-agent-interface.md))
- **Date:** 2026-10-21
- **Deciders:** Barloom maintainers

## Context

Barloom runs strategy scripts as Python in its own process, with the user's privileges. That is fine for code the user wrote or chose. An agent, though, can be mistaken or manipulated by text it reads, and could write a strategy that reads files, opens connections, or deletes data the moment a simulation runs. Agents need to try their own ideas, so their code has to run somewhere. The user also required that every limit be enforced by code, not by the sandbox alone, because models can be jailbroken.

## Decision

**Provenance by content hash (Part 6).**

- Every script version an agent writes is recorded in `.barloom/script-provenance.json` as `agent-unreviewed`, together with the connection that wrote it.
- The record is written before the file is saved, so the file is never on disk unmarked.
- A user's own edit changes the hash, so the edited file is the user's code.

**One check at the loader.** `engine._load_strategy` looks up every file's hash before running it, so the rule holds however a run was started, and even if a new code path forgets the sandbox:

- An unreviewed agent version never runs in Barloom's process.
- Static simulations (`CellRuntime(..., sandbox_unreviewed=True)`, used by Static runs and suites) get a `SandboxedStrategy` instead.
- Feed and Live never pass that flag, so loading such a script there is refused with `script_unreviewed`.

**The sandbox (Part 10)** runs the strategy in a child process (`barloom/sandbox_child.py`). The engine sees an ordinary strategy: `on_bar`, plus `snapshot`/`restore` when the strategy has them. Each call becomes one JSON message, and only bars the child hasn't seen are sent. The layers are independent:

1. **Code policy**, checked before any process starts:
   - imports only from math, statistics, collections, itertools, functools, dataclasses, typing, datetime, random, bisect, heapq, operator, fractions, decimal, and numpy when installed;
   - none of `open`, `exec`, `eval`, `compile`, `__import__`, `getattr`/`setattr`/`delattr`, `globals`, `locals`, `vars`, `input`, `breakpoint`, `__builtins__`;
   - no dunder attributes other than `__init__`, `__name__`, `__doc__`;
   - no `global` or `nonlocal`.

   `script-check` reports these problems as `sandbox_problems`.
2. **An isolated child**, started with `python -I` in an empty temporary folder. Its environment has only what Python needs to start, so no API or broker keys. Its stdout is replaced, so anything the strategy prints cannot reach the protocol.
3. **A deny-by-default audit hook.** The allowed modules are imported first, then the hook is installed. While the strategy's source loads, the hook allows only `exec`, `compile`, and imports of allowed modules. After that it refuses every audited event: files, sockets, subprocesses, `os.system`, directory listings, `ctypes`, and new imports.
4. **Resource limits.**
   - On Windows, a job object allows one process, 1 GiB of memory, and 300 s of CPU, and kills the process when the job closes.
   - Elsewhere, rlimits cap memory and CPU and forbid writing files.
5. **Timeouts.** Each call has a timeout (10 s, and 30 s to start). A strategy that overruns is killed.

**Review lets a version run anywhere (Part 9).** On the Agents page the user reads the source of a waiting version and marks it reviewed. Only the browser app can do this; the command is not open to agent tokens. A reviewed version runs as normal code, in-process, in every mode.

**Testing each layer alone.** `tests/test_sandbox.py` switches the code policy off and attacks the runtime layer directly: files, `os` calls, subprocesses, imports, secrets in the environment, printing into the protocol, runaway loops, and memory. The loader check is tested without the tools in front of it: a direct Feed deploy of unreviewed code is refused.

## Limits, stated plainly

These layers stop the realistic failures: a mistaken agent, or one following injected instructions to read keys or reach the network. They are not a boundary against a determined attacker who controls the code and attacks CPython itself; some interpreter internals are unaudited. A container- or WASM-based runner can replace the child behind `SandboxedStrategy` later without changing callers. Feed and Live, which run unattended, never run unreviewed code at all.

## Alternatives considered

| Choice | Benefit | Cost or reason not chosen |
| --- | --- | --- |
| Run the whole simulation job in the child | One process; no per-call messages | The child would hold Barloom's own file and database access while the untrusted code runs |
| Refuse unreviewed code everywhere | Simplest | Agents could not test their own ideas, the main point of drafting code |
| AST policy only | No processes | One layer; anything the static check misses runs with full privileges |
| Docker or WASM now | Stronger isolation | A heavy dependency for a local, standard-library tool; can come later behind the same interface |

## Consequences

- Each sandboxed strategy is one short-lived process per cell and run, which adds a fraction of a second.
- Agent strategies are limited to the allowed modules. User-reviewed code has no such limit.
- Anything the policy rejects must be rewritten, or reviewed by the user, before it can run.
