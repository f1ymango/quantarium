# Security review: v0.3.0

- **Date:** 2026-10-21
- **Scope:** everything added between v0.2.2 and v0.3.0: about 4,100 lines across 35 files, covering the agent switch, connections and tiers, the audit log, budgets, script endpoints, `quantarium mcp`, the sandbox, proposals and the Inbox, holdout scenarios, the built-in assistant, its provider adapters, and the Codex bridge.
- **Method:** reading the code path by path against the threat model below; probing suspected gaps with working attacks; a real Codex bridge turn on a ChatGPT plan; and regression tests for every fix (`tests/test_security.py`, plus `tests/test_injection.py` and `tests/test_sandbox.py`).

## Threat model

Quantarium serves on loopback for one user. The review considered five attackers:

1. **A website open in the user's browser.** It can send requests to `localhost` but cannot read cross-origin responses, set custom headers without a CORS preflight, or pass the `Host` check from a rebound DNS name.
2. **A manipulated or mistaken AI agent** that holds an agent token, or runs as the built-in assistant or a bridge. It may follow instructions planted in workspace content.
3. **Untrusted strategy code** that an agent wrote.
4. **Leaks of secrets:** API keys, broker credentials, and agent tokens.
5. **Path escapes** out of the workspace or into `.quantarium/`.

Out of scope: other software running as the same user with shell or file access. It can read workspace files directly, including the page that embeds the browser token (ADR-0014).

## Findings and fixes

| # | Severity | Finding | Fix |
| --- | --- | --- | --- |
| F1 | High | `GET jobs/{id}` returned the parameters and results of any job to an agent. That included the user's own Static runs on holdout data, which defeated the holdout guarantee. | Jobs record the agent connection that submitted them. For anyone else's job, an agent sees only id, action, status, and times. |
| F2 | High | Holdout checks missed `run-static` with a config file, `GET config` on such a config, and Feed-kind reads (`analysis`, `report`, `timeline`, `holdings`) of an experiment fed by a holdout scenario. | Those routes now check the config's data path and the experiment's source against holdout scenarios. |
| F3 | Medium | The sandbox's code policy let through frame and code introspection without dunders (`gi_frame`, `f_builtins`, …), `str.format` (which reads dunders at runtime), `operator.attrgetter`/`methodcaller`, and numpy's file functions. numpy's `fromfile` reads files in C, below Python's audit hook. Probes with the policy switched off showed the audit hook still refused `open`, `os.system`, and new imports; the numpy path depends on the policy. | The policy bans those attribute names. |
| F4 | Medium | Provenance matched by content alone, so an agent that wrote a copy of the user's own script made the user's original look unreviewed. Feed then refused it, which let an agent break the user's experiments. | Provenance entries apply only to the paths an agent wrote. |
| F5 | Medium | `.quantarium/agents.json` was read, modified, and written with no lock. A revocation racing a bridge's token issue could be lost. | One lock serializes every write; a test races revocations against token churn. Provenance writes are serialized too. |
| F6 | Low | A negative `Content-Length` made a request thread wait forever for a body. | Refused with `400`. |
| F7 | Low | The Codex thread id stored in a chat file is placed on Codex's command line. A tampered file could have passed a flag in its place. | Only a UUID is accepted, both when stored and when used. |

Found and fixed earlier in v0.3.0, before this review:
- Script provenance tracked through a context variable (concurrent requests could have attributed an agent's write to the user).
- Agents refused during the tutorial.
- Agents kept out of hidden folders.
- The sandbox started with the base interpreter (a venv launcher broke the one-process limit).
- The Codex bridge's tool access fixed without broadening approvals.

## What was checked and held

- **Browser boundary.** Writes need the per-session token, a JSON content type, and Quantarium's own Origin. Bearer-token requests need the `Host` check and are refused with a foreign Origin; a website cannot add an `Authorization` header without a CORS preflight, which the server never answers. The pages contain no raw-HTML sinks (`innerHTML` and similar), so model and agent text always renders as text. Assistant references render as Quantarium components, and their only links are hash routes with encoded keys.
- **The agent path.** Every agent operation passes the switch, pause, token or connection, tier (allow-list: anything unlisted is refused), path rules, holdout guard, and budget, then the audit log, before any handler runs. Browser-only commands (approve, review, holdout, keys, chats, grants, the switch, the tutorial) are absent from the allow-list, and they also refuse a calling agent in code.
- **Secrets.** Tokens are stored as SHA-256 digests and compared in constant time. API keys are read from the environment or the keychain at call time and are never written, logged, returned, or included in provider errors. The sandbox and Codex get environments without Quantarium, Anthropic, OpenAI, or Alpaca variables. The audit log redacts by key name and by known key shapes.
- **Code execution.** Unreviewed agent code never runs in-process: the loader refuses it outside Static, and Static runs it in the sandbox. The policy, audit hook, resource limits, and timeouts were each tested alone.
- **Prompt injection.** Planted instructions in a scenario, a cell name, and a script reach the agent. An agent with every tier, and an assistant that obeys the text, achieve none of the planted goals.

## Accepted risks

- **Same-user software** with shell or file access is outside what Quantarium can bound (ADR-0014). For Claude Code or the Codex CLI as external agents, their own permission prompts are the boundary.
- **Codex's command runner** cannot be turned off in Codex 0.157. Quantarium stops a bridge turn as soon as Codex starts a command; anything that ran first ran in Codex's read-only sandbox (ADR-0020).
- **The bridge's per-turn token** appears on Codex's command line, where other local processes can read it while the turn runs. It is valid for that turn only.
- **Codex loads the user's `~/.codex/AGENTS.md`** in bridge chats, and nothing turns it off. Quantarium's limits don't depend on model instructions.
- **The sandbox is not a hard boundary** against an attacker who targets CPython itself (ADR-0016). Feed and Live never run unreviewed agent code.
- **Reads without a token.** Browser reads, including chats and the audit log, need no token, as in v0.2. Websites cannot read them; local software can.
- **Waiting requests.** `GET jobs/{id}?wait=` holds a server thread for up to 120 seconds. An agent could tie up threads within its budgets; this is local only.
