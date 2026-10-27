# ADR-0020: Subscription bridges

- **Status:** Accepted for the Codex bridge (Part 16 of the [v0.3.0 plan](../plans/v0.3.0-agent-interface.md)). Superseded in part by [ADR-0022](0022-agent-runtime-port.md) in v0.3.1: the bridge is now the `codex-exec` profile in `barloom/runtimes/codex_exec.py`, and Claude Code is an experimental profile.
- **Date:** 2026-10-21
- **Deciders:** Barloom maintainers

## Context

The user wants to use the built-in assistant with a ChatGPT plan, not only with API keys. Providers' consumer plans are tied to their official apps. Copying a plan's sign-in into another app violates their terms and can get the user's account suspended. Barloom therefore never signs in to a plan itself. It can, however, run the provider's own runtime, which signs in by itself.

## Decision

**The Codex bridge** (`barloom/bridges.py`) is a connection of `kind: "bridge"`, `bridge: "codex"`. Each assistant turn runs the installed Codex CLI as `codex exec --json` (or `codex exec resume <thread> --json` after the first turn) and turns its JSONL events into chat messages as they arrive. Codex runs the agent loop under the user's ChatGPT plan.

**Barloom's tools only.**

- Codex is given one MCP server, `barloom mcp` for this workspace, with a token valid for that turn only. The token is issued as a digest on the connection, expires after two hours at most, and is withdrawn when the turn ends.
- Every tool call is therefore an agent request with that connection's grant, budgets, holdout rules, and audit log.
- `--ignore-user-config` and `--ignore-rules` keep the user's other Codex MCP servers and rules out. Authentication still comes from the user's Codex sign-in.

**Codex's own tools are switched off** with `--disable`:

- shell, browser and external browser, in-app browser, computer use;
- apps, plugins, image generation and viewing, standalone web search;
- plus `web_search="disabled"`.

The code mode host stays on: in Codex 0.157 every tool call, Barloom's included, runs through it, and with it off no tool works. Barloom's MCP server alone is set to `default_tools_approval_mode="approve"`, because `approval_policy="never"` otherwise refuses MCP calls that need approval. Barloom checks every call itself, and nothing else is pre-approved.

Codex runs read-only (`sandbox_mode="read-only"`), never asks for approvals, gives commands no inherited environment, and starts in an empty temporary folder. Barloom's, Anthropic's, OpenAI's, and broker variables are removed from its environment.

**The one thing Barloom cannot switch off.** In Codex 0.157 the command runner (`unified_exec`) stays on whatever the flags say, as `codex features list` shows. So the moment Codex reports a command starting, Barloom kills the turn and tells the user. Anything that ran before the stop ran inside Codex's read-only sandbox with no inherited environment. The Agents page says that an API-key connection gives the strongest isolation.

**What a chat leaves behind.**

- Barloom's copy in `.barloom/chats/`.
- An empty temporary folder per chat.
- Codex's own transcript in `~/.codex/sessions/` (or `$CODEX_HOME`), which Codex needs in order to resume the thread.

Deleting a chat in Barloom removes the folder. It can also remove Codex's transcript of that thread, if the user ticks the box; Barloom matches the transcript exactly by thread id inside Codex's sessions folder and touches no other file. OpenAI's copy follows the plan's terms.

**The user's own Codex instructions still apply.** Codex always loads the global `~/.codex/AGENTS.md`, even with `--ignore-user-config`, and no setting turns that off. A persona or a house style there shows up in Barloom chats too. Barloom's limits don't depend on the model's instructions, so they are unaffected.

**Verification.** Tests use a fake `codex` (`tests/fake_codex.py`) to check the following without using anyone's plan:

- the arguments Barloom builds;
- that the per-turn token works during a turn and is refused after it;
- that the environment is clean;
- that resuming works;
- that a command stops the turn.

`codex features list` with Barloom's flags checks that the real Codex accepts the feature names. A real turn on a ChatGPT plan, run before release, found the two settings above: the code mode host, and approval for Barloom's tools. With them, Codex called `barloom.workspace_overview`, and Barloom checked and logged the call.

**The Claude Code bridge** would start the user's installed Claude Code headless (`claude -p`) the same way. It ships only after Anthropic's documentation confirms that this use of a subscription is permitted. Until then, Claude users connect with an Anthropic API key, or use Claude Code or Claude Desktop as external agents.

## Alternatives considered

| Choice | Benefit | Cost or reason not chosen |
| --- | --- | --- |
| The Codex app server (JSON-RPC) | Richer events, one long-lived process | Its protocol changes between versions; `codex exec --json` is the documented non-interactive interface |
| Sign in to ChatGPT from Barloom | No Codex install | Against provider terms; risks the user's account |
| Let Codex use its shell, read-only | More capable | Shell access goes around every Barloom limit; Barloom's tools are the boundary |

## Consequences

- The bridge depends on the Codex CLI's flags and event names. A test checks the flags against `codex features list` wherever Codex is installed, and unknown events are ignored.
- Usage counts against the user's ChatGPT plan, as Codex reports it.
- Each turn starts a Codex process and a `barloom mcp` process, so a turn takes a few seconds longer than a direct connection.
