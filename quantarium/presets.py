"""Connection presets: the model APIs and agent apps Quantarium knows by name.

Presets are data in ``assets/agents/presets.json``, so adding a provider needs
no code. A preset only fills in defaults; any compatible endpoint or ACP
agent works through the custom presets. This module also holds the endpoint
rules every model API connection must pass.
"""

from __future__ import annotations

import ipaddress
import json
import urllib.parse
from functools import lru_cache
from importlib import resources

PROTOCOLS = ("openai-chat", "anthropic-messages")
PROFILES = ("codex-exec", "claude-code", "acp")
LOOPBACK_NAMES = {"localhost"}


@lru_cache(maxsize=1)
def load() -> dict:
    text = resources.files("quantarium").joinpath("assets/agents/presets.json").read_text(encoding="utf-8")
    return json.loads(text)


def prices() -> dict:
    """The dated table of model prices, in US dollars per million tokens."""
    text = resources.files("quantarium").joinpath("assets/agents/prices.json").read_text(encoding="utf-8")
    return json.loads(text)


def price_for(preset_id: object, model: object) -> dict | None:
    """A model's listed prices, zero for a server on this computer, or None when the table does not list it."""
    table = prices()
    if preset_id in table.get("free_presets", []):
        return {"input_usd_per_million": 0.0, "output_usd_per_million": 0.0, "checked": table["checked"]}
    found = next((item for item in table["prices"] if item["preset"] == preset_id and item["model"] == model), None)
    if found is None:
        return None
    return {"input_usd_per_million": float(found["input_usd_per_million"]),
            "output_usd_per_million": float(found["output_usd_per_million"]), "checked": table["checked"]}


def api_preset(preset_id: object) -> dict | None:
    return next((item for item in load()["api"] if item["id"] == preset_id), None)


def runtime_preset(preset_id: object) -> dict | None:
    return next((item for item in load()["runtimes"] if item["id"] == preset_id), None)


def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    # Only names that never leave this computer. Other *.localhost names may be
    # sent to a DNS server, and a key over plain HTTP would follow the answer.
    if host.lower() in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def check_endpoint(url: object) -> str:
    """A model API endpoint keys may be sent to: HTTPS, or plain HTTP on this computer only."""
    if not isinstance(url, str) or not 8 <= len(url) <= 300 or any(ord(char) < 33 for char in url):
        raise ValueError("endpoint must be a URL such as https://api.example.com/v1")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in {"https", "http"} or not parts.hostname:
        raise ValueError("endpoint must start with https://")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("endpoint may not contain a user name, password, query, or fragment")
    if parts.scheme == "http" and not is_loopback(parts.hostname):
        raise ValueError("Keys are sent only over HTTPS; plain http:// is allowed only for servers on this computer")
    return url.rstrip("/")


def endpoint_host(url: str | None) -> str | None:
    return urllib.parse.urlsplit(url).hostname if url else None
