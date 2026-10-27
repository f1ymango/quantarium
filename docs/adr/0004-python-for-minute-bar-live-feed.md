# ADR-0004: Keep the minute-bar engine in Python for version 0.1

- **Status:** Accepted pending measurement with a real market session
- **Date:** 2026-10-20
- **Deciders:** Barloom maintainers; implementation choice recorded for user review

## Context

Live Feed may run many independent cells at once, and the user raised a concern that Python memory overhead could grow enough to justify moving to C++ early. Barloom currently shares one bounded minute history per symbol, processes cells serially in one process, and writes their account events to SQLite. `tracemalloc` measures Python allocations but excludes native SQLite and interpreter working-set costs, so it alone is insufficient for this choice.

## Decision

Keep the Live Feed engine in Python for version 0.1 and measure whole-process resident memory alongside Python allocations. Preserve bounded shared history and one market subscription. Optimize a measured bottleneck before considering a C++ component or language rewrite.

On this Windows development machine, separate synthetic runs of 500 symbols and 60 minutes with a history limit of 60 reported:

| Cells | Processing time | Peak Python allocations | Peak process resident memory | SQLite file |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 4.72 s | 5.61 MiB | 49.52 MiB | 10.49 MiB |
| 16 | 20.99 s | 7.33 MiB | 56.87 MiB | 37.72 MiB |

The process resident measure includes the interpreter and native libraries, so it is more relevant to the laptop than `tracemalloc`. The increased cell count had a larger effect on processing time and saved journal size than on peak memory in this probe. The probe generated all bars locally, used a simple equal-weight strategy, and did not open a WebSocket; it does not establish real provider capacity or custom strategy performance.

## Alternatives considered

| Choice | Benefit | Cost or reason deferred |
| --- | --- | --- |
| Rewrite the whole simulator in C++ now | More control over memory layout and event throughput | Significant implementation and review cost without a measured Python bottleneck at one-minute cadence |
| Put every cell in a separate Python process | Stronger isolation from slow or crashing scripts | Duplicates interpreter state and market history, increasing the very memory cost under review |
| Add NumPy or pandas to the Live Feed core | Efficient batch numerical operations | The current engine is event-driven with small per-minute updates; a broad dependency does not address a measured bottleneck here |
| Keep Python and shared bounded history | Fast iteration with measured memory within this probe's range | A slow custom strategy can still delay every cell; SQLite size and CPU grow with cells and symbols |

## Consequences and follow-up

The benchmark now reports peak process resident memory where the operating system exposes it, plus Python allocations and SQLite size. These measurements should be repeated with the intended laptop, realistic strategy scripts, and a real permitted market feed. If the process misses its minute deadlines or exceeds an acceptable memory budget, profile the actual hot path and consider a targeted compiled component before a full rewrite. Provider subscription limits may prevent a 500-symbol Live Feed on a given account; Feed Batch remains the large-portfolio forward-testing path.
