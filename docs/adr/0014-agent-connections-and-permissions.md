# ADR-0014: Agent connections and permissions

- **Status:** Accepted. It covers the agent switch and notice (Part 1 of the [v0.3.0 plan](../plans/v0.3.0-agent-interface.md)) connections, tokens, and tiers (Part 2); and budgets, pausing, and the audit log (Part 3).
- **Date:** 2026-10-21
- **Deciders:** Barloom maintainers

## Context

v0.3.0 lets AI agents read a workspace, write strategy scripts, run simulations, and propose changes. Barloom will later trade real accounts, and agents send workspace content to an outside AI provider, so users must choose this knowingly. The user required that every agent feature be strictly disabled by default, that the limits be enforced by code rather than by a model's behavior, and that users be told plainly what they take responsibility for.

## Decision: the agent switch

**Off by default, per workspace.** `barloom/agents.py` keeps the setting in `.barloom/agents.json`. Agent features are on only when the file says `enabled: true` *and* records acceptance of the current `NOTICE_VERSION`. A missing, unreadable, or unexpected file reads as off.

**One gate.** `AgentSettings.require_enabled()` raises `AgentsDisabled`, which the server turns into `403` with `"code": "agents_disabled"`. Every agent-facing endpoint added later calls it before any other work, so nothing agent-facing is reachable while the switch is off. API errors gain an optional machine-readable `code` for this, and later errors will use it too.

**Informed, typed acceptance.** The notice (`barloom/assets/agents/notice.md`) covers:

- the software is provided as is, without warranty or liability;
- nothing in Barloom is financial advice;
- data goes to the provider the user chooses;
- the user is responsible for complying with provider and broker terms, and for their costs;
- AI can be wrong or manipulated;
- how to keep credentials safe;
- how to turn agent features off.

Enabling requires the user to type the phrase "I understand and accept" for the notice version they were shown. It works from the Agents page or from `barloom agents enable`.

- The CLI refuses unless stdin is an interactive terminal, so a script or an agent with a shell cannot pipe the confirmation in.
- The app's command requires the per-session browser token; agent tokens are refused for it (Part 2).

**Notice changes need new acceptance.** Raising `NOTICE_VERSION` turns agent features off in every workspace until the user accepts the new text.

**The switch belongs to the real workspace.** It is handled before the tutorial state routes requests to the tutorial's private workspace, so starting the tutorial neither hides nor resets it.

## Decision: connections, tokens, and tiers

**A connection is one agent client and its grant.**

- The user creates one in the app; the `agent-connection-create` command requires the browser token and agent features on.
- Barloom returns a random bearer token (`blm_…`) once and stores only its SHA-256 in `.barloom/agents.json`.
- The grant is a set of tiers: `observe`, `draft`, `simulate`, `propose`, and `manage`. The default is all but `manage`.
- Revoking a connection stops its token at once.

**Agent requests carry `Authorization: Bearer <token>` and pass through one function.** The server sends every such request to `Api.agent_request`. It runs three checks before any handler:

1. agent features are on;
2. the token belongs to an active connection;
3. `AgentSettings.authorize` finds the operation in the `OPERATIONS` table and finds its tier in the grant.

Anything not in the table is refused with `403 not_permitted`. That covers deleting cells, the tutorial, the agent switch, connections, and grants, so new endpoints are closed to agents until someone deliberately lists them.

Current mapping:

- reads → `observe`;
- creating, editing, and duplicating cells → `draft`;
- scenario jobs, Static runs, and suites → `simulate`;
- archiving, restoring, retiring, and Advancing Feed → `manage`.

Advance stays `manage` until Barloom records which experiments an agent created.

**Browser rules are not loosened.** Requests with a bearer token skip the browser token, but they must still pass the Host check and send JSON. They are refused if they carry an Origin other than Barloom's own. A web page cannot add an `Authorization` header to a cross-origin request without a CORS preflight, which the server never grants.

**What tokens can and cannot protect against.** Tokens and tiers bound an agent that reaches Barloom only through its API: the built-in assistant, a bridge whose shell tools are off, or an MCP client without shell access. They cannot bound software that runs as the user with a shell or file access. Such a process can read `.barloom/agents.json`, edit workspace files directly, or fetch the page that embeds the browser token. For external agents like Claude Code with their shell enabled, that agent's own permission prompts are the boundary. The Agents page and the README say so.

## Decision: the audit log, budgets, and pausing

**Every agent request is logged once.** `barloom/agent_log.py` appends to `.barloom/agent-log.sqlite`, whatever the outcome: `ok`, `refused` (a switch, token, tier, or budget check said no), or `error` (the handler failed). Each entry records the time, connection, operation, parameters, status, error code and message, job id, and duration. Requests with an unknown token are logged with no connection.

Secrets are removed before saving:

- values of keys that look like keys, tokens, secrets, passwords, or credentials;
- strings shaped like Barloom, Anthropic, OpenAI, or AWS keys.

The app shows the log on the Agents page (`GET agents/log`). An agent can read only its own connection's entries (`GET agent-log`), which helps it resume work later.

**Budgets are counted from the log.** Each connection has `jobs_per_hour` (default 60) and `cells_per_day` (default 50, counting creates and duplicates). The values are set when the connection is created. Only successful requests count, so refused ones never use up a budget. A request over budget gets `429 budget_exceeded`.

A budget on simulated sessions per day waits for summary modes (Part 5), which report session counts.

**Pause all** (`agents-pause`, `agents-resume`) refuses every agent token with `403 agents_paused`. Agent features and connections stay as they are, so resuming needs no new acceptance.

## Alternatives considered

| Choice | Benefit | Cost or reason not chosen |
| --- | --- | --- |
| Store tokens in plain text so `barloom mcp` can read them | No copy-paste step | Any reader of the workspace gets every connection's grant; a digest leaks nothing |
| A global setting in the user's home folder | One switch for every workspace | Consent and the data sent are per workspace; a shared setting would silently enable agents in workspaces the user never reviewed |
| A checkbox instead of a typed phrase | Faster | Easy to click without reading; a typed phrase is a clearer signal of intent |
| `barloom agents enable --yes` for scripts | Automatable | Lets an agent with a shell enable itself |
| Keep the notice only in docs | One copy | The app and the CLI must show the exact versioned text the user accepts, so it ships in the package |

## Consequences

- A fresh clone, and every existing workspace, has agent features off.
- Setting up a CI machine or another non-interactive machine needs the app, or a person at a terminal.
- Every later part of v0.3.0 builds its agent endpoints behind this gate, and their tests must include the refused-while-off case.
- Changing the notice's meaning requires raising `NOTICE_VERSION`. Typo fixes can keep the version.
