# ADR-0017: Proposals and approvals

- **Status:** Accepted (Parts 11 and 12 of the [v0.3.0 plan](../plans/v0.3.0-agent-interface.md))
- **Date:** 2026-10-21
- **Deciders:** Quantarium maintainers

## Context

Some actions matter more than a simulation:

- starting a Feed forward test, which runs unattended and later becomes the gate for real money;
- advancing an experiment;
- retiring a cell from one;
- archiving or restoring cells.

An agent should be able to recommend them without being able to do them. The user must see exactly what will happen, and what they approve must be exactly what runs.

## Decision

**Agents file proposals; only the app decides.**

- The command `proposal-create` needs the `propose` tier. Its body is one of `deploy-feed`, `feed-batch`, `retire-cell`, `cell-archive`, or `cell-restore`, with parameters, a rationale, and evidence (job ids or short notes).
- These actions stay out of the agent `OPERATIONS` table, so an agent cannot run them directly.
- `proposal-decide` (approve or reject, with an optional note) is not open to agent tokens. It is also refused whenever a calling agent is present, so it needs the browser token.
- Approving runs the action as the user, through the normal job or command path.

**Checked when filed.** A proposal must pass the same checks as acting directly:

- the path rules for agents;
- the action's own parameter validation, run without executing (the cells exist and are ready, files exist);
- the `proposals_per_day` budget (default 20).

A bad proposal fails immediately instead of reaching the user.

**Pinned, then compared.** Each proposal records:

- for each cell it names, a fingerprint of its name, settings, and archived flag, plus its version;
- the SHA-256 of every script those cells use.

At approval the pins are recomputed. Any difference marks the proposal `stale` and refuses it, so an agent cannot swap a script or edit a cell between the user's look and the user's click.

**Approving a forward test reviews its scripts.** Feed never runs unreviewed agent code (ADR-0016). Approving a `deploy-feed` proposal marks exactly the pinned script versions reviewed, which is why the Inbox shows their source, and then starts the experiment.

**Agents can follow up.** `GET proposals` with an agent token lists that connection's own proposals: status, the job an approval started, the user's note on a rejection. The MCP tools are `propose_feed_test`, `propose_advance`, `propose_archive`, `propose_restore`, `propose_retire`, and `list_proposals`.

## Alternatives considered

| Choice | Benefit | Cost or reason not chosen |
| --- | --- | --- |
| Let `manage` connections run Feed directly | Fewer clicks | Feed is the step before real money; it should always have a human look |
| Approve by proposal id only, without pins | Simpler | The approved action could differ from the one the user read |
| A separate review step for each script in a Feed proposal | More explicit | The proposal already shows the source; one decision covers what the user saw |

## Consequences

- Proposals live in `.quantarium/proposals.json`, which is out of agent reach (ADR-0014 path rules).
- A proposal that names a file outside cells, such as `feed-batch` on a config, pins no scripts. Its config's own hashes (Feed's settings check) still guard it.
- The Inbox page (Part 12) is where approval happens. The assistant's chat (Part 14) links to proposals but has no approve button.
