# ADR-0030: Declared information boundaries before model binding

- **Status:** Accepted as v0.4.0 groundwork; verification belongs to the Lab and model-card milestones.
- **Date:** 2026-10-22
- **Deciders:** Quantarium maintainers
- **Review:** ChatGPT-6 Sol

## Context

An ML model's last training feature date is not necessarily the last date whose outcome it learned. A label can depend on later prices. Historical sessions on or before that final label date cannot establish out-of-sample performance for the model. Quantarium needs to carry that boundary through daily runs before model cards exist.

## Decision

- A cell may declare `information_end` as an ISO date. The configuration parser validates it and Feed Batch pins it as part of the cell settings so it cannot change during a continuing experiment.
- Static and Feed Batch write the boundary into the strategy event and annotate each valuation with `post_information_end`, true only when the session date is later than the boundary. Static run metadata also exposes the date.
- The source is explicitly `cell_config_unverified`. A user can type an earlier date, so the declaration alone cannot earn a Lab verdict. The later model-card binding must provide and verify the date from the model's training record.
- Cells without a declared boundary keep their existing journal and simulation behavior. The Lab will classify their evidence separately; absence of a date is not proof of clean data.

## Consequences

- A run can still display and explore sessions before the boundary. The Lab must exclude them from a verdict.
- Feed Batch can resume without allowing a cell's declared boundary or starting holdings to change silently.
- Live Feed has no model-driven `Decision` support in this milestone and does not infer a training boundary.
