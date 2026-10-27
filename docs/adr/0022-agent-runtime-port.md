# ADR-0022: A provider-neutral agent runtime port, and Claude Code (experimental)

- **Status:** Accepted (v0.3.1, [plan](../plans/v0.3.x-model-connections.md)). The Claude Code profile is experimental.
- **Date:** 2026-10-21
- **Deciders:** Barloom maintainers
- **Supersedes in part:** [ADR-0020](0020-subscription-bridges.md), whose Codex bridge becomes the `codex-exec` profile.

## Context

Subscriptions (ChatGPT, Claude, and others) work only through each vendor's own app. Copying a plan's sign-in into another app violates the vendors' terms. v0.3.0 therefore ran the user's own Codex CLI as a bridge, with code written for Codex alone. More agent apps are coming: Claude Code, OpenCode, GitHub Copilot, Gemini CLI, and others. Many of them speak the **Agent Client Protocol** (ACP), an open JSON-RPC protocol between a client and a coding agent running on stdio.

Anthropic's terms forbid third parties from offering Claude.ai sign-in, routing requests through plan credentials, or collecting Claude credentials. The same page says this does not prevent "an end user from signing in to the unmodified Claude Code binary with their own Claude subscription". Running Claude Code in a product requires the binary to be unmodified, its sign-in methods unrestricted, and usage billed to the end user. Anthropic has not said whether an open-source app on the user's own computer counts as a product.

## Decision

**One port, several profiles.** A `bridge` connection (shown as "Agent app") names a runtime preset from `presets.json`, which picks a profile in `barloom/runtimes/`. Every profile implements `run_turn`, `transcripts`, and, through the package, transcript removal. Shared code gives every turn:

- a token valid for that turn only, withdrawn when it ends;
- `barloom mcp` as the only MCP server;
- an empty temporary folder per chat;
- an environment without Barloom's, the broker's, or model API keys, including every variable a model API connection reads;
- cleanup of the app's pipes and Barloom's per-turn files.

Every tool call is therefore an agent request with the connection's tiers, budgets, holdout rules, and audit log, whatever the app.

**`acp`: any ACP agent.** The user gives a command, run without a shell. Barloom:

- offers no client capabilities: no file reads, file writes, or terminals;
- answers `session/request_permission` by allowing Barloom's own tools and rejecting every other (Barloom names its tools as the agents do, such as `mcp__barloom__list_cells` or `list_cells (barloom MCP Server)`, and never allows a tool whose kind acts on the computer);
- stops the turn when the agent reports a tool call that is not Barloom's;
- refuses requests it did not offer;
- cancels with `session/cancel` when the user stops;
- resumes with `session/load` where the agent supports it, and otherwise sends a short recap.

**`codex-exec`: Codex.** The v0.3.0 bridge (ADR-0020), unchanged in behaviour and moved behind the interface.

**`claude-code`: Claude Code, experimental.** Barloom runs the user's installed, unmodified `claude` CLI headless, with these flags:

| Flag | Effect |
| --- | --- |
| `-p --output-format stream-json --verbose` | Headless mode with structured events |
| `--tools ""` | Removes every built-in tool: no shell, files, edits, or web |
| `--mcp-config <file> --strict-mcp-config` | Only Barloom's MCP server, from a per-turn file that holds the token and is deleted after the turn, so the token is never on the command line |
| `--allowedTools mcp__barloom --permission-mode dontAsk` | Barloom's tools only; anything else is refused without asking |
| `--setting-sources project,local` in the empty folder | The user's hooks, permissions, and other MCP servers are not loaded |
| `--disable-slash-commands` | No skills |
| `--session-id` / `--resume` | One Claude Code session per chat |

If Claude Code's start event lists a tool other than Barloom's (or its MCP resource readers), or a reply uses one, Barloom stops the turn.

Claude Code signs in by itself, with whatever the user set up in it. Barloom never reads, stores, or passes on that sign-in, and does not remove any sign-in method. Model API keys are left out of the environment, so a plan sign-in is not silently replaced by an API key.

**The user's decision and responsibility.** Creating a Claude Code connection requires the user to accept an acknowledgement, recorded on the connection with its time and a digest of its text:

> I choose to connect my own Claude Code to Barloom. I have read Anthropic's terms, and any action Anthropic takes on my account because of this use is my own responsibility, not Barloom's or its authors'.

Barloom provides the technical path, within what Anthropic's terms describe. Whether to use a Claude plan this way is the user's choice. The preset card says who bills the usage, where conversations are kept, and the terms status.

The profile is permanently experimental: it is provided as is, it is not verified against real Claude accounts, and the maintainers do not plan to promote it. Any runtime preset that is both experimental and a subscription path must carry an acknowledgement, which the preset test checks.

**App settings in presets (v0.3.2).** A runtime preset may set `env`: environment variables for the app during Barloom turns. The OpenCode preset uses `OPENCODE_CONFIG_CONTENT`, which OpenCode applies over its other configuration. It turns OpenCode's own tools off, denies them, and sets every other permission to ask, so each tool call becomes a permission request that Barloom answers. The setting is data in `presets.json`, and the preset test refuses secret-looking and Barloom variable names.

## Consequences

- New agent apps that speak ACP work through **Custom ACP agent** with no Barloom release, and become presets in the v0.3.x sub-versions.
- An agent app is software the user installed. ACP gives Barloom permission requests and tool reports, not control of the app. An app set to run its own tools without asking could act on the computer before Barloom sees the report; Barloom then stops the turn. As for external agents (ADR-0014), Barloom bounds what an agent does through Barloom, and the app's own settings bound the rest.
- Claude Code loads the user's `~/.claude/CLAUDE.md`, as Codex loads `~/.codex/AGENTS.md`. Barloom's limits do not depend on model instructions.
- Claude Code keeps its own transcript of each chat in `~/.claude/projects/` (or `$CLAUDE_CONFIG_DIR`). Deleting the chat in Barloom can remove it; Barloom matches it by session id and touches no other file.
- If Anthropic's position changes, the preset can be removed from `presets.json` without touching the port.
