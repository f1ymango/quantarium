# ADR-0035: Judging LLM and hybrid supercells

- **Status:** Accepted for v0.4.5
- **Date:** 2026-10-24
- **Deciders:** Quantarium maintainers

## Context

v0.4.4 lets a strategy ask a language model (ADR-0034). Two questions decide whether its results mean anything. First, could the model remember the history it was tested on? Second, in a hybrid that also uses a risk card (ADR-0032), what does each part add? Until v0.4.5, the Lab answered the first question bluntly: an asking cell earned a verdict only on synthetic scenarios.

## Decision

**Training cutoffs, recorded per connection.** A model API connection may record its model's training cutoff, as the provider discloses it (`agent-connection-cutoff`, user only). Changing the connection's model clears it. Lab registration pins the asking model and its cutoff, and a changed cutoff makes the registration stale.

**Lineage.** Registration records whether each scenario comes from real history:

- historical scenarios are `historical`;
- synthetic scenarios are `synthetic`;
- a transformed scenario takes its parent's lineage;
- a transformed scenario whose parent cannot be found is `unknown`.

Transformations keep real dates, so a real crash with drift added is still real history.

**Model memory per scenario,** for a candidate with an ask grant:

| Lineage | Recorded cutoff | Label |
| --- | --- | --- |
| synthetic | any | clean |
| historical | the scenario starts after the cutoff | clean |
| historical | the scenario starts on or before the cutoff | contaminated |
| historical | none | unknown |
| unknown | any | unknown |

The verdict is `unknown` unless every scenario is clean. A candidate with a grant counts as asking even in a record without a pinned cutoff, so older ledger entries fail closed. The cutoff is the provider's disclosure as typed by the user, and the verdict's caveats say so.

**Ablation, without knowing the strategy.** The Lab adds a variant for each part a candidate has:

- **without the language model:** whenever the script calls `context.ask`, the call raises instead of reaching a model, and that sleeve keeps its last target for the session;
- **without the risk card:** `context.risk_estimates` is empty and no estimates are journaled.

Each part's contribution is the candidate's return minus its variant's. When a part's mean contribution is not positive, a caveat says so. Ablation is diagnostic and does not change the verdict. It is a Lab device: Feed and Live refuse cells with `ablation`.

**The disguised replay, optional per experiment** (`disguised_replay`). For each scenario that is not synthetic, Quantarium writes a disguised copy under `.quantarium/lab-disguised/`:

- invented four-letter symbols;
- the same number of NYSE sessions starting in 2041;
- each symbol's prices multiplied by one factor between 0.5 and 2.

Returns, intraday ranges, and session order are kept. The copy's provenance records its parent's hash but not its name. A copy of the candidate runs on it, with its symbols, starting holdings, and string parameters renamed. The Lab reports the return gap per scenario.

The gap is a diagnostic and never changes the verdict. A strategy may legitimately use price levels or dates, and whole-share rounding changes slightly with the price factor. A candidate that cannot run on the copy, for example because it hard-codes a ticker, is reported as possibly depending on the real symbols. A cell that asks a model makes new calls for the copies.

## Found by testing on a real model

A Lab run with qwen3:4b through Ollama found two gateway problems, fixed in this version:

- When a reasoning model used its whole output budget thinking, it returned no answer, and the gateway reported "not valid JSON". It now says the model ran out of output tokens.
- A call without a valid answer was never written to the tape, so its tokens and cost were not counted. A failing model could spend without limit. Failed calls are now appended as `failed` entries under their own keys, so they count against every budget and are never replayed as answers.

## Consequences

- An asking cell can earn a verdict on real history, but only on periods after a cutoff the user recorded.
- Hybrids report what each part adds on the same scenarios, at no extra model cost for the variant without the model.
- The disguised replay roughly doubles the model calls for real-history scenarios, within the grant's budgets.
