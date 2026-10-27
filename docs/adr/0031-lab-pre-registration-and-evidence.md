# ADR-0031: Pre-registered Lab experiments and scoped verdicts

- **Status:** Accepted for v0.4.1
- **Date:** 2026-10-22
- **Deciders:** Quantarium maintainers
- **Review:** ChatGPT-6 Sol

## Context

The existing Static suite compares cells across scenarios but lets a researcher choose a pass rule after seeing results. Repeated variants on the same data can make a lucky result look convincing. The Lab needs a reviewable account of what was tested, when the rule was set, and which data were already used.

## Decision

- Register exactly one candidate cell, one to twenty non-holdout scenarios, simulated costs, and a pass rule before running. Pin the cell version and definition, direct strategy file hashes, scenario manifest, prices, and action hashes. A changed input makes the registration stale; it does not silently rerun new inputs.
- Run the candidate and two disclosed controls (buy-and-hold and daily equal-weight) through the existing scenario suite engine with the same starting cash, holdings, market data, and execution costs. Store the full suite report in a local ledger at `.quantarium/lab.json`. No candidate result is shown at registration.
- A scenario family is traced through transformations and source slices. Once a run starts, its families are marked used for that idea. Later variants may run, but their verdict is `unknown` and explicitly exploratory. The ledger counts attempts visible in this workspace, including stale and failed ones, while warning that offline or renamed attempts are unknowable.
- A registered rule can yield `supported_within_tested_scope`, `not_supported`, `inconclusive`, or `unknown`. The verdict includes each scenario's period and provenance, both controls, the candidate universe, costs, boundary status, and caveats. A self-declared `information_end` is unverified and yields `unknown` until model-card binding exists.
- An agent with the propose tier may propose registration. It cannot register or run the experiment itself. The Inbox approval pins scenarios in addition to cell and script inputs. Agents may read the Lab ledger subject to normal observe permissions, but holdout scenarios remain excluded.

**Amended in v0.4.3.** A rule passes only when the mean edge over the best control *exceeds* `min_mean_excess_return_pp`. A candidate within 0.000001 percentage points of its best control in every scenario is `not_supported`, because matching a simple control is not evidence for an idea. Verdict rows carry the candidate's error when it did not complete.

## Alternatives considered

- **Make the existing suite view the Lab.** This would blur exploratory tests with frozen tests. The Lab sits beside Static and reuses its engine instead.
- **Silently refuse a second test of the same idea and data.** Researchers need to inspect variants. The Lab allows a run but withholds a fresh verdict.
- **Report a multiple-testing-adjusted Sharpe now.** Quantarium cannot observe offline trials or reliably estimate an effective trial count. It reports the observed count and limitation; any later adjustment needs the full required inputs.
- **Trust a typed information cutoff.** A date in a cell is not evidence of the latest training label. The boundary stays visible but unverified.

## Consequences and limits

The ledger and pinned files are local and can be edited outside Quantarium. Direct strategy files are hashed, while transitive imports and undisclosed data reads are not proven safe. Source families are conservative where provenance exists; two separately imported copies of the same market data may not be recognized as related. A passing scenario result is confined to those sessions, symbols, controls, costs, and Quantarium's execution assumptions. It does not establish future returns.
