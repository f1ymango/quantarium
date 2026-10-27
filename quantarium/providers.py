"""How the built-in assistant reaches a model.

A model API connection names a protocol (``openai-chat`` or
``anthropic-messages``), an endpoint, and a model, so any compatible server
works, not only the vendors Quantarium has presets for. Every adapter takes the
same request (a system prompt, the conversation in Quantarium's neutral form,
and the tools) and returns the same reply: content blocks of ``text`` and
``tool_use``, and token usage.

Keys are read from an environment variable or the system keychain when a
call is made, never stored by Quantarium, and never put into messages, logs, or
errors. A key is sent only to its connection's own endpoint, only over HTTPS
unless the server is on this computer, and redirects are never followed.
"""

from __future__ import annotations

import os
import time

from . import presets
from .errors import QuantariumError

NO_KEY = "no-key"  # the SDKs need a value even for local servers that ignore it


class ProviderError(QuantariumError):
    def __init__(self, message: str, code: str = "provider_failed", hint: str | None = None) -> None:
        super().__init__(message, code, hint)


def read_key(connection: dict) -> str:
    """The connection's API key, from where the user said it is."""
    if connection.get("key_store") == "none":
        return NO_KEY
    if connection.get("key_env"):
        key = os.environ.get(connection["key_env"])
        if not key:
            raise ProviderError(f"The environment variable {connection['key_env']} is not set for Quantarium",
                                "provider_key_missing",
                                "Set it in the environment that starts quantarium serve, then restart it")
        return key
    try:
        import keyring
    except ImportError:
        raise ProviderError("The keyring package is not installed", "provider_key_missing",
                            "Install it with: py -m pip install keyring, or use an environment variable")
    key = keyring.get_password("quantarium", connection["id"]) or keyring.get_password("barloom", connection["id"])
    if not key:
        raise ProviderError("No key is stored in the system keychain for this connection",
                            "provider_key_missing", "Store one on the Agents page")
    return key


def endpoint(connection: dict) -> str:
    """The connection's endpoint, checked again before any key is sent to it."""
    try:
        return presets.check_endpoint(connection.get("endpoint"))
    except ValueError as exc:
        raise ProviderError(str(exc), "provider_endpoint", "Create the connection again with a valid endpoint")


def label(connection: dict) -> str:
    """How errors name the endpoint: its preset's name, or its host for a custom one."""
    preset = presets.api_preset(connection.get("preset"))
    if preset and preset["id"] != "custom":
        return preset["name"]
    return presets.endpoint_host(connection.get("endpoint")) or "The endpoint"


def http_client(sdk):
    """An HTTP client for an SDK that never follows a redirect, so a key cannot be carried elsewhere."""
    return sdk.DefaultHttpxClient(follow_redirects=False)


def capabilities() -> dict:
    """What this computer can use for the assistant, for the Agents page."""
    import importlib.util
    import shutil

    def installed(name: str) -> bool:
        return importlib.util.find_spec(name) is not None

    apps = {item["id"]: bool(item["command"]) and shutil.which(item["command"][0]) is not None
            for item in presets.load()["runtimes"]}
    return {"anthropic_sdk": installed("anthropic"), "openai_sdk": installed("openai"),
            "keyring": installed("keyring"), "codex": apps.get("codex", False), "apps": apps}


def store_key(connection: dict, key: object) -> dict:
    """Put an API key in the system keychain for a connection that keeps it there."""
    if connection.get("key_store") != "keyring":
        raise ProviderError("This connection does not keep its key in the keychain", "invalid_parameter")
    if not isinstance(key, str) or not 8 <= len(key.strip()) <= 500:
        raise ProviderError("That does not look like an API key", "invalid_parameter")
    try:
        import keyring
    except ImportError:
        raise ProviderError("The keyring package is not installed", "provider_key_missing",
                            'Install it with: py -m pip install "quantarium[assistant]"')
    keyring.set_password("quantarium", connection["id"], key.strip())
    return {"stored": True}


def forget_key(connection: dict) -> None:
    if connection.get("key_store") != "keyring":
        return
    try:
        import keyring
        keyring.delete_password("quantarium", connection["id"])
    except Exception:  # absent key or keyring: nothing to forget
        pass
    try:
        import keyring
        keyring.delete_password("barloom", connection["id"])
    except Exception:
        pass


def provider_for(connection: dict):
    """The provider object for an assistant connection."""
    if connection.get("kind") == "bridge":
        from .runtimes import runtime_for

        return runtime_for(connection)
    if connection.get("protocol") == "anthropic-messages":
        from .providers_anthropic import AnthropicProvider

        return AnthropicProvider(connection)
    if connection.get("protocol") == "openai-chat":
        from .providers_openai import OpenAIProvider

        return OpenAIProvider(connection)
    raise ProviderError(f"No adapter for the protocol {connection.get('protocol')}", "provider_unavailable")


PING_TOOL = {"name": "ping", "description": "Checks the connection. Always call it when asked.",
             "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}}


def check_connection(connection: dict, provider=None) -> dict:
    """One small request with one tool: can this endpoint and model call tools, and how fast?"""
    provider = provider or provider_for(connection)
    started = time.monotonic()
    reply = provider.complete("You are checking a connection. Call the ping tool once, then stop.",
                              [{"role": "user", "content": [{"type": "text", "text": "Call the ping tool."}]}],
                              [PING_TOOL], max_tokens=1024)
    seconds = round(time.monotonic() - started, 2)
    called = any(block.get("type") == "tool_use" and block.get("name") == "ping" for block in reply["content"])
    return {"ok": called, "tool_calls": called, "seconds": seconds, "model": connection.get("model"),
            "message": "The model called a tool, so it can work as the assistant." if called else
            "The endpoint answered, but the model did not call the tool. The assistant needs a model that "
            "supports tool calls; choose another model."}


def list_models(connection: dict, provider=None) -> dict:
    """The models the endpoint lists, where it supports listing them."""
    provider = provider or provider_for(connection)
    models = provider.models()
    return {"models": sorted(set(models))[:500]}
