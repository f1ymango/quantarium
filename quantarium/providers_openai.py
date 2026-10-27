"""The ``openai-chat`` protocol adapter, through the official OpenAI SDK.

Chat Completions is the most widely implemented model API: OpenAI, OpenRouter,
Groq, Mistral, DeepSeek, Gemini, xAI, and local servers such as Ollama and
LM Studio all speak it. The SDK is an optional dependency:
``pip install "quantarium[assistant]"``. Quantarium's conversations are stored in
a neutral block form (text, tool_use, tool_result); this adapter converts
them to chat messages and back.
"""

from __future__ import annotations

import json

from .presets import endpoint_host
from .providers import ProviderError, endpoint, http_client, label, read_key

MAX_TOKENS = 16000
OFFICIAL_HOST = "api.openai.com"


def _to_chat(system: str, messages: list[dict]) -> list[dict]:
    chat = [{"role": "system", "content": system}]
    for message in messages:
        blocks = message["content"]
        if message["role"] == "user":
            results = [block for block in blocks if block.get("type") == "tool_result"]
            for block in results:
                chat.append({"role": "tool", "tool_call_id": block["tool_use_id"], "content": block["content"]})
            texts = [block["text"] for block in blocks if block.get("type") == "text"]
            if texts:
                chat.append({"role": "user", "content": "\n\n".join(texts)})
            continue
        text = "".join(block["text"] for block in blocks if block.get("type") == "text")
        calls = [{"id": block["id"], "type": "function",
                  "function": {"name": block["name"], "arguments": json.dumps(block.get("input") or {})}}
                 for block in blocks if block.get("type") == "tool_use"]
        entry: dict = {"role": "assistant", "content": text or None}
        if calls:
            entry["tool_calls"] = calls
        chat.append(entry)
    return chat


def _to_tools(tools: list[dict]) -> list[dict]:
    return [{"type": "function", "function": {"name": tool["name"], "description": tool["description"],
                                              "parameters": tool["input_schema"]}} for tool in tools]


class OpenAIProvider:
    def __init__(self, connection: dict, client=None) -> None:
        self.connection = connection
        self.model = connection["model"]
        self._client = client
        # OpenAI renamed max_tokens; most other servers still read the old name.
        self.official = endpoint_host(connection.get("endpoint")) == OFFICIAL_HOST

    def _sdk_client(self):
        if self._client is not None:
            return self._client
        try:
            import openai
        except ImportError:
            raise ProviderError("The OpenAI SDK is not installed", "provider_sdk_missing",
                                'Install it with: py -m pip install "quantarium[assistant]"')
        return openai.OpenAI(api_key=read_key(self.connection), base_url=endpoint(self.connection),
                             max_retries=2, timeout=180, http_client=http_client(openai))

    def complete(self, system: str, messages: list[dict], tools: list[dict], max_tokens: int = MAX_TOKENS) -> dict:
        client = self._sdk_client()
        limit = {"max_completion_tokens": max_tokens} if self.official else {"max_tokens": max_tokens}
        try:
            response = client.chat.completions.create(
                model=self.model, messages=_to_chat(system, messages),
                **({"tools": _to_tools(tools)} if tools else {}), **limit)
        except Exception as exc:
            raise _translate(exc, label(self.connection)) from None
        if not getattr(response, "choices", None):
            raise ProviderError(f"{label(self.connection)} returned no answer", "provider_failed")
        choice = response.choices[0]
        message = choice.message
        content = []
        if message.content:
            content.append({"type": "text", "text": message.content})
        for call in message.tool_calls or []:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except ValueError:
                arguments = {"invalid_arguments": call.function.arguments}
            content.append({"type": "tool_use", "id": call.id, "name": call.function.name, "input": arguments})
        if choice.finish_reason == "content_filter":
            content.append({"type": "text", "text": "The model's provider filtered this response."})
        if not content:
            # Some models, small local ones especially, stop after thinking with nothing to say.
            content.append({"type": "text", "text": "The model returned an empty reply. Try again, or choose a "
                            "larger model; Test connection on the Agents page checks tool calls."})
        usage = response.usage
        return {"content": content, "stop": choice.finish_reason,
                "usage": {"input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                          "output_tokens": getattr(usage, "completion_tokens", 0) or 0}}

    def models(self) -> list[str]:
        client = self._sdk_client()
        try:
            return [item.id for item in client.models.list()]
        except Exception as exc:
            raise _translate(exc, label(self.connection)) from None


def _translate(exc: Exception, name: str = "OpenAI") -> ProviderError:
    """Turn an SDK error into a Quantarium error, without ever including the key."""
    try:
        import openai
    except ImportError:
        return ProviderError(f"{name} request failed: {type(exc).__name__}")
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and 300 <= status < 400:
        return ProviderError(f"{name} answered with a redirect, which Quantarium does not follow with your key",
                             "provider_endpoint", "Check the connection's endpoint URL")
    if isinstance(exc, openai.AuthenticationError):
        return ProviderError(f"{name} rejected the API key", "provider_auth",
                             "Check the key this connection reads, then try again")
    if isinstance(exc, openai.PermissionDeniedError):
        return ProviderError("The API key may not use this model", "provider_auth")
    if isinstance(exc, openai.NotFoundError):
        return ProviderError(f"{name} does not know this model name, or the endpoint is wrong", "provider_model",
                             "Edit the connection's model on the Agents page")
    if isinstance(exc, openai.RateLimitError):
        return ProviderError(f"{name}'s rate limit or quota was reached", "provider_rate_limited",
                             "Wait a minute, or check the account's quota")
    if isinstance(exc, openai.APIStatusError):
        return ProviderError(f"{name} returned {exc.status_code}: {getattr(exc, 'message', '')}"[:300])
    if isinstance(exc, openai.APIConnectionError):
        return ProviderError(f"Could not reach {name}", "provider_unreachable",
                             "Check the network connection and the endpoint URL; a local server must be running")
    return ProviderError(f"{name} request failed: {type(exc).__name__}")
