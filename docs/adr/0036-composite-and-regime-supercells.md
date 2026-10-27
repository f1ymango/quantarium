# ADR-0036: Composite and regime supercells

- **Status:** Accepted for v0.4.6
- **Date:** 2026-10-24
- **Deciders:** Barloom maintainers

## Context

A Complex cell combines several scripts (sleeves) with fixed shares of the money. The plan's last step (section 5) is supercells that allocate between strategies, or switch between them by market regime. Research on the `ml-signals` branch suggests where value may lie: risk estimates were accurate, but acting on them directly did not beat simple controls. Deciding *which* strategy runs, rather than predicting prices, is a natural next test.

## Decision

**An allocator decides the shares at each close.** A Complex cell may name an allocator: a class with `allocate(context, sleeves)`.

- It receives the context (bars, history, positions, risk estimates) and one `Sleeve` per script, in order, with that script's own target weights and its configured share.
- It returns one fraction per sleeve, each from 0 to 1, totalling at most 1.
- The engine combines the sleeves' weights with those fractions instead of the fixed shares. The rest of the decision is unchanged: weight limits, the cash reserve, and next-open execution.
- The signal event records the allocation.

A regime supercell is an allocator that gives everything to one sleeve. A composite supercell is any other split.

**Built-in allocators** (`barloom/allocators.py`):

- `FixedAllocation`: the configured shares, unchanged.
- `TrendRegime`: everything to one sleeve while a symbol closes above its moving average, and to another otherwise. Until the window fills, the money goes to the risk-off sleeve.

**Where allocators come from.** A cell's `spec.allocator` names either a built-in (`{"builtin": true, "class": ..., "parameters": ...}`) or a class in a workspace script beside the sleeves' scripts (`{"script": ..., "class": ..., "parameters": ...}`). Script discovery lists allocator classes separately from strategy classes.

**Reviewed code only.** Unreviewed agent-written strategies run in the sandbox during Static runs. An unreviewed agent-written allocator is refused outright, never sandboxed, because the sandbox's protocol carries strategy decisions only. Agents' path rules apply to the allocator's script inside a cell spec. The Lab and proposals pin its file hash with the sleeves'.

**Models in allocators.** An `ask` grant (ADR-0034) may name the allocator's script, so a language model can choose the regime. In the Lab's variant without the model, the allocator keeps its last split whenever it would have asked.

**The Lab.** A candidate with an allocator also runs with fixed shares, and the verdict reports what the allocator added. This is the honest test of a regime switch: does switching beat holding both strategies at their usual shares?

**Feed and Live.** An allocator's state is checkpointed for Feed when it has `snapshot` and `restore`; cells without one keep the old checkpoint shape. Live Feed refuses allocators until minute-bar timing is designed. A `Decision` needs a cell with one strategy and no allocator.

## Consequences

- Regime and composite strategies are tested against their own fixed-share version as well as the usual controls.
- An allocator sees the sleeves' proposed weights but cannot change them; it only chooses how much each sleeve gets.
- Allocators are trusted local code once reviewed, like strategies. The sandbox does not run them.
