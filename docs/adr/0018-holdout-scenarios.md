# ADR-0018: Holdout scenarios and variant counting

- **Status:** Accepted (Part 13 of the [v0.3.0 plan](../plans/v0.3.0-agent-interface.md))
- **Date:** 2026-10-21
- **Deciders:** Quantarium maintainers

## Context

An agent can simulate hundreds of variants in minutes. The best of many tries always looks better than it will do going forward: it fits the noise in the scenarios it was tuned on. Quantarium can't stop an agent from searching, but it can do two things. It can keep an honest test set out of the agent's reach, and it can tell the user, and the model, how hard the search was.

## Decision

**Holdout scenarios.**

- The user marks scenarios as holdout on their Scenario page (`scenario-holdout`, which only the app can call). The list lives in `.quantarium/holdout.json`, out of agent reach.
- For agent requests, the server refuses with `403 holdout` anything that would read, run on, or reveal a holdout scenario:
  - any path parameter inside a holdout scenario folder: scenario reads, transforms, suites, Static runs on its prices, proposals;
  - suite reports and suite runs whose configs include a holdout scenario;
  - Feed Advances fed from one;
  - analysis, reports, timelines, and holdings of a Static run on holdout data.
- Listings drop holdout scenarios from `workspace` and Feed experiments fed by them from `arena`. Cell cards and cell pages drop deployments with holdout results.

**Recognized by content.** A run is a holdout run when its price-data hash (stored with every Static run) or its suite metadata's scenario manifest hash matches a holdout scenario's current content. A copy or renamed folder therefore stays hidden: the agent can run on the copy, but cannot read its results.

**One holdout test per cell version.** An agent may file a `holdout-test` proposal naming cells.

- When approved, Quantarium runs a suite on all holdout scenarios and records each cell's settings fingerprint.
- A second test of the same settings is refused (`holdout_used`). Changing the cell makes a new variant, which may be tested once.
- Only the user sees the results. The agent learns only that the proposal was approved and which job ran.

**Variant counting.**

- Each agent Static run or suite records the settings fingerprint of every cell it ran, per connection, in the agent log database.
- Summaries returned to an agent carry `variants_tested`, and a caveat once it reaches 20.
- Each proposal records the count when filed, and the Inbox shows it ("The agent had simulated 140 cell variants…").

## Alternatives considered

| Choice | Benefit | Cost or reason not chosen |
| --- | --- | --- |
| Hide holdout scenarios by name only | Simple | A copy under another name would leak |
| Let agents see holdout results after a holdout test | More useful feedback | The agent would tune against the test set; one look per version keeps the test meaningful only if the agent never sees it |
| Hard-limit variants per day | Stops excess search | The count matters more than a cap, and budgets already limit jobs per hour |

## Consequences

- Holdout checks cost a hash of each holdout scenario's prices, cached by file modification time.
- A suite that mixes holdout and visible scenarios is hidden from agents entirely.
- Results the user creates on holdout data stay visible to the user everywhere.
