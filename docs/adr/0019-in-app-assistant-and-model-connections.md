# ADR-0019: The in-app assistant and model connections

- **Status:** Accepted (Parts 14 and 15 of the [v0.3.0 plan](../plans/v0.3.0-agent-interface.md))
- **Date:** 2026-10-21
- **Deciders:** Barloom maintainers

## Context

The user wants everything to fit inside the Barloom dashboard: a chat with their own Claude or OpenAI model that can build, test, and manage cells. External agents already reach Barloom through `barloom mcp` (ADR-0015), with limits enforced by the server (ADR-0014, 0016–0018). The assistant must have exactly those limits, not a second, weaker copy.

## Decision

**One tool catalog, one agent path.**

- The assistant offers the model the same tools as `barloom mcp` (`barloom/mcp.py` `TOOLS`), filtered to the tiers the connection's grant allows.
- Each tool call is translated by the same function (`tool_operation`) and executed by `Api.agent_call`. That is the same checks, budgets, holdout rules, and audit log as `Api.agent_request`, entered by connection id instead of a token.
- A test runs the same tool through both paths and compares results and audit entries.

**Assistant connections are connections.** `agent-connection-create` takes `kind`:

- `external` — a token for MCP clients;
- `direct` — the assistant calls a model API;
- `bridge` — a subscription runtime (ADR-0020).

Direct connections have no token. Their settings name the provider (`anthropic` or `openai`), the model, and where the key lives:

- `key_env` — the name of an environment variable;
- `key_store: "keyring"` — the OS keychain, through the optional `keyring` package. The app's `agent-connection-key` command passes the key straight to the keychain.

Barloom never writes a key to a file, a log, a response, or an error message. Revoking a connection removes its keychain entry.

**Providers through their official SDKs, as an optional extra.** `pip install "barloom[assistant]"` installs `anthropic`, `openai`, and `keyring`; the core stays standard-library only. Each adapter makes one request per step, because Barloom runs the tool loop itself so it can check, log, save, and stop each step.

- **Anthropic** (`providers_anthropic.py`):
  - the Messages API via `client.beta.messages.create`;
  - default model `claude-opus-5-5` (also Sonnet 5.5, Haiku 5.5, Fable 5.1);
  - `output_config.effort: "high"` for agentic tool use; thinking left at its adaptive default;
  - a cached system prompt and tool list;
  - server-side refusal fallback (`fallbacks: "default"`) on models that support it.

  Response content blocks, including thinking blocks, are stored and sent back unchanged. A `refusal` stop adds a plain note; `pause_turn` continues.
- **OpenAI** (`providers_openai.py`): Chat Completions with function tools. The user names the model. Barloom's neutral blocks (text, `tool_use`, `tool_result`) are converted to chat messages and back.
- SDK errors become Barloom codes such as `provider_auth`, `provider_model`, `provider_rate_limited`, `provider_unreachable`, and `provider_key_missing`.

**The panel.**

- A launcher and a docked chat on every page except during the tutorial.
- Each message carries the current page as context. Chats are stored in `.barloom/chats/` with token usage.
- Replies render as text only. `[[cell:ID]]`, `[[job:ID]]`, `[[proposal:ID]]`, and `[[scenario:KEY]]` become Barloom components, such as a live cell card.
- Tool calls show as collapsible steps; a filed proposal links to the Inbox.
- There is no approve control in the chat. The model has no tool for it, and the chat commands and `proposal-decide` refuse any calling agent.
- A turn stops after 25 steps or when the user presses Stop.

## Alternatives considered

| Choice | Benefit | Cost or reason not chosen |
| --- | --- | --- |
| The assistant calls `barloom mcp` over stdio | One transport for everything | An extra process per chat for no gain; the in-process path is the same checks |
| Raw HTTP clients instead of the SDKs | No dependency | The SDKs handle retries, typed errors, and wire details; keeping them optional preserves the core's no-dependency rule |
| Streaming replies | Text appears as it is written | More moving parts; each step's text appears when the step ends, which is acceptable for a first version |
| Store keys encrypted in the workspace | Works without keyring | A key next to the data it guards; environment variables and the OS keychain are the standard places |

## Consequences

- The assistant needs agent features on and a direct or bridge connection; otherwise the panel explains how to set one up.
- Model usage is billed to the user's provider account, and the panel shows token counts.
- A new tool becomes available to the assistant and to MCP clients at once.
