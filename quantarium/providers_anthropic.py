"""The ``anthropic-messages`` protocol adapter, through the official Anthropic SDK.

It works with Anthropic's API and with any gateway that speaks the Messages
API at another endpoint. The SDK is an optional dependency:
``pip install "quantarium[assistant]"``. Quantarium runs the tool loop itself
(every call passes its agent checks), so the adapter makes one request per
step. Response content blocks are kept and sent back unchanged, so thinking
blocks are preserved across steps.
"""

from __future__ import annotations

from .presets import endpoint_host
from .providers import ProviderError, endpoint, http_client, label, read_key

MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1")
DEFAULT_MODEL = "claude-opus-5-5"
MAX_TOKENS = 16000
OFFICIAL_HOST = "api.anthropic.com"
# Agentic, multi-step tool use benefits from deeper effort than the default.
EFFORT = "high"
# Models with server-side fallback when a safety classifier declines.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    def __init__(self, connection: dict, client=None) -> None:
        self.connection = connection
        self.model = connection["model"]
        self._client = client
        # Effort, caching, and fallbacks are Anthropic's; gateways may refuse them.
        self.official = endpoint_host(connection.get("endpoint")) == OFFICIAL_HOST

    def _sdk_client(self):
        if self._client is not None:
            return self._client
        try:
            import anthropic
        except ImportError:
            raise ProviderError("The Anthropic SDK is not installed", "provider_sdk_missing",
                                'Install it with: py -m pip install "quantarium[assistant]"')
        return anthropic.Anthropic(api_key=read_key(self.connection), base_url=endpoint(self.connection),
                                   max_retries=2, timeout=180, http_client=http_client(anthropic))

    def complete(self, system: str, messages: list[dict], tools: list[dict], max_tokens: int = MAX_TOKENS) -> dict:
        client = self._sdk_client()
        request = {"model": self.model, "max_tokens": max_tokens, "system": system,
                   "messages": messages, **({"tools": tools} if tools else {})}
        if self.official:
            # The system prompt and tool list are stable, so cache them.
            request.update(cache_control={"type": "ephemeral"}, output_config={"effort": EFFORT})
            if self.model in FALLBACK_MODELS:
                request.update(betas=[FALLBACK_BETA], fallbacks="default")
        try:
            response = client.beta.messages.create(**request) if self.official else client.messages.create(**request)
        except Exception as exc:
            raise _translate(exc, label(self.connection)) from None
        content = [block.model_dump(exclude_none=True) for block in response.content]
        if response.stop_reason == "refusal":
            detail = getattr(response, "stop_details", None)
            category = getattr(detail, "category", None)
            content.append({"type": "text", "text": "The model declined to continue this request"
                            + (f" ({category})." if category else ".")})
        usage = response.usage
        return {"content": content, "stop": response.stop_reason,
                "usage": {"input_tokens": (usage.input_tokens or 0)
                          + (getattr(usage, "cache_read_input_tokens", 0) or 0)
                          + (getattr(usage, "cache_creation_input_tokens", 0) or 0),
                          "output_tokens": usage.output_tokens or 0}}

    def models(self) -> list[str]:
        client = self._sdk_client()
        try:
            return [item.id for item in client.models.list(limit=100)]
        except Exception as exc:
            raise _translate(exc, label(self.connection)) from None


def _translate(exc: Exception, name: str = "Anthropic") -> ProviderError:
    """Turn an SDK error into a Quantarium error, without ever including the key."""
    try:
        import anthropic
    except ImportError:
        return ProviderError(f"{name} request failed: {type(exc).__name__}")
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and 300 <= status < 400:
        return ProviderError(f"{name} answered with a redirect, which Quantarium does not follow with your key",
                             "provider_endpoint", "Check the connection's endpoint URL")
    if isinstance(exc, anthropic.AuthenticationError):
        return ProviderError(f"{name} rejected the API key", "provider_auth",
                             "Check the key this connection reads, then try again")
    if isinstance(exc, anthropic.PermissionDeniedError):
        return ProviderError("The API key may not use this model", "provider_auth")
    if isinstance(exc, anthropic.NotFoundError):
        return ProviderError(f"{name} does not know this model name, or the endpoint is wrong", "provider_model",
                             "Edit the connection's model on the Agents page")
    if isinstance(exc, anthropic.RateLimitError):
        return ProviderError(f"{name}'s rate limit was reached", "provider_rate_limited", "Wait a minute and retry")
    if isinstance(exc, anthropic.APIStatusError):
        return ProviderError(f"{name} returned {exc.status_code}: {getattr(exc, 'message', '')}"[:300])
    if isinstance(exc, anthropic.APIConnectionError):
        return ProviderError(f"Could not reach {name}", "provider_unreachable",
                             "Check the network connection and the endpoint URL")
    return ProviderError(f"{name} request failed: {type(exc).__name__}")
