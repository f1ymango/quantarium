# ADR-0021: A provider-neutral model API port

- **Status:** Accepted (v0.3.1, [plan](../plans/v0.3.x-model-connections.md))
- **Date:** 2026-10-21
- **Deciders:** Quantarium maintainers

## Context

In v0.3.0 a model API connection named a provider, `anthropic` or `openai`, and each had its own adapter. Quantarium will be open source, and users want many more models: other vendors, gateways such as OpenRouter, and models running on their own computer. Per-vendor code would not scale and would leave out every endpoint Quantarium has not heard of.

Two API shapes cover almost every model service. OpenAI's Chat Completions is implemented by OpenAI, OpenRouter, Groq, Mistral, DeepSeek, Gemini, xAI, OpenCode Zen, and local servers such as Ollama, LM Studio, and vLLM. Anthropic's Messages API is the other.

## Decision

**A connection names a protocol, not a vendor.** A `direct` connection stores:

- `protocol`: `openai-chat` or `anthropic-messages`;
- `endpoint`: the API's base URL;
- `model`;
- where the key is: `key_env` (an environment variable), `key_store: "keyring"`, or `key_store: "none"` for a local server;
- `preset`: the preset it was made from, for display.

Each protocol has one adapter on its official SDK (`providers_openai.py` and `providers_anthropic.py`), with the SDK's `base_url` set to the endpoint.

**Presets are data.** `quantarium/assets/agents/presets.json` lists the APIs Quantarium knows by name: Anthropic, OpenAI, OpenRouter, Groq, Mistral, DeepSeek, Ollama, LM Studio, and Custom. A preset gives a name, protocol, endpoint, the usual key variable, known models, whether the endpoint can list models, who bills the usage, and a docs link. Contributors add a provider by adding an entry. A test validates the file, so a bad entry fails CI rather than the Agents page. **Custom** takes any endpoint and either protocol.

**Vendor extras only where they belong.** Effort, prompt caching, and server-side fallback are sent only to `api.anthropic.com`, and `max_completion_tokens` only to `api.openai.com`. Gateways and other servers get the plain protocol, which they are more likely to accept.

**Endpoint safety.** A key leaves Quantarium only to the endpoint its connection names, so:

- endpoints must use HTTPS, except for servers on this computer (loopback addresses, `localhost`, and `*.localhost`);
- an endpoint may not carry a user name, password, query, or fragment;
- the SDKs' HTTP clients never follow redirects, so a redirect cannot carry the key to another host; Quantarium reports the redirect instead;
- when the endpoint's host differs from its preset's, the Agents page shows the host the key will go to before the connection is created;
- errors name the preset or host and never include the key (the v0.3.0 rule).

**Discovery and Test connection.** `agent-connection-models` lists the endpoint's models (`GET /models`) so the user can pick one. `agent-connection-test` sends one small request with a single `ping` tool. It reports whether the model called the tool and how long it took. The assistant needs tool calls, and small local models often handle them poorly, so this checks it for real. Both commands are app-only.

**Migration.** v0.3.0 connections (`provider: "anthropic"` or `"openai"`) are read as the matching preset, protocol, and endpoint, and are written in the new form at the next change to `agents.json`. `agent-connection-create` still accepts the old `provider` form.

## Consequences

- Any compatible endpoint works without a Quantarium release, and new presets are one JSON entry.
- Quantarium's safety does not depend on the provider. Every tool call still goes through the agent checkpoint (ADR-0014, ADR-0019).
- Quantarium cannot vouch for a custom endpoint's handling of the key or the conversation. The Agents page names the host, and the preset card says who bills the usage.
- Compatibility differs between servers (tool-call formats, token limits, error shapes). Test connection shows problems before a chat does.
