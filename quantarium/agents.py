"""The workspace switch for agent features, and the connections agents use.

Agent features are off by default and on only after the user accepts the
current notice. Every agent-facing capability calls
``AgentSettings.require_enabled`` before doing anything, so nothing
agent-facing is reachable while the switch is off. The switch lives in
``.quantarium/agents.json``. A new ``NOTICE_VERSION`` turns agent features off
until the user accepts the new notice.

The same file holds agent connections. Each has a bearer token, stored only
as its SHA-256, and a grant of tiers. ``authorize`` is the one place that
decides what a connection may do; operations it does not list are refused.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import threading
from datetime import date, datetime, timedelta, timezone
from importlib import resources

from . import presets

SETTINGS_FILE = ".quantarium/agents.json"
NOTICE_VERSION = 1
CONFIRMATION = "I understand and accept"
TIERS = ("observe", "draft", "simulate", "propose", "manage")
DEFAULT_TIERS = ("observe", "draft", "simulate", "propose")
MAX_CONNECTIONS = 20
# Budgets stop a looping agent from filling the disk or the job queue.
DEFAULT_BUDGETS = {"jobs_per_hour": 60, "cells_per_day": 50, "proposals_per_day": 20, "decisions_per_day": 500}
MAX_BUDGET = 10_000
# Connection kinds: an external MCP client with a token, the built-in
# assistant calling a model API ("direct"), or the assistant running in an
# agent app such as Codex or Claude Code ("bridge").
KINDS = ("external", "direct", "bridge")
# v0.3.0 connections named a provider; they now name a protocol and endpoint.
LEGACY_PROVIDERS = {"anthropic": "anthropic", "openai": "openai"}
MAX_COMMAND_ARGS = 20
_ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,63}")
_MODEL = re.compile(r"[^\x00-\x1f]{1,200}")

# What each operation needs. Anything absent (deleting cells, the tutorial,
# the agent switch, and connections themselves) is never allowed for an
# agent token. "propose" has no operations until proposals exist.
OPERATIONS = {
    "get": {route: "observe" for route in (
        "health", "workspace", "analysis", "timeline", "report", "config", "scenario",
        "suite-report", "jobs", "cells", "scripts", "holdings", "arena", "lab",
        # The connection's own entries in the agent log, and its own proposals.
        "agent-log", "proposals", "decision-inboxes")},
    "command": {"cell-create": "draft", "cell-save": "draft", "cell-duplicate": "draft",
                "script-write": "draft", "script-check": "observe", "proposal-create": "propose",
                "cell-archive": "manage", "cell-restore": "manage",
                # Forward decisions: stamped on arrival, counted only if before the next open.
                "decision-inbox-create": "simulate", "decision-submit": "simulate"},
    "job": {"scenario-generate": "simulate", "scenario-transform": "simulate",
            "scenario-slice": "simulate", "run-static": "simulate", "run-suite": "simulate",
            "deploy-static": "simulate", "deploy-suite": "simulate",
            # Advancing Feed changes a forward test. Limiting it to the agent's
            # own experiments needs ownership records, so it is manage for now.
            "feed-batch": "manage", "retire-cell": "manage"},
}
PREFIX_ROUTES = {"jobs/": "observe", "cells/": "observe", "scripts/": "observe", "lab/": "observe"}
# Routes answered per connection by Api.agent_request, not by Api.get.
CONNECTION_ROUTES = frozenset({"agent-log", "proposals"})
# Parameters that name workspace files. Agents never reach hidden folders
# such as .quantarium/, where grants, logs, and provenance live.
PATH_PARAMS = ("db", "config", "source", "data", "output", "key", "actions")
# Agents write scripts only here, unless a connection may edit the user's own.
AGENT_SCRIPT_FOLDER = "strategies/agent/"


class AgentError(Exception):
    """A refused agent request; ``status`` is the HTTP status to return."""

    status = 403
    code = "not_permitted"


class AgentsDisabled(AgentError):
    """An agent-facing request arrived while agent features are off."""

    code = "agents_disabled"


class AgentUnauthorized(AgentError):
    status = 401
    code = "unknown_token"


class AgentsPaused(AgentError):
    code = "agents_paused"


class HoldoutRefused(AgentError):
    code = "holdout"


class BudgetExceeded(AgentError):
    status = 429
    code = "budget_exceeded"


def notice_text() -> str:
    return resources.files("quantarium").joinpath("assets", "agents", "notice.md").read_text(encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _check_model(model: object) -> str:
    if not isinstance(model, str) or not _MODEL.fullmatch(model.strip()):
        raise ValueError("model must be the provider's model name")
    return model.strip()


def _api_settings(provider: dict) -> dict:
    """A model API connection: a protocol, an endpoint, a model, and where the key is."""
    if "provider" in provider and "preset" not in provider:  # the v0.3.0 form
        provider = {**{key: value for key, value in provider.items() if key != "provider"},
                    "preset": LEGACY_PROVIDERS.get(provider["provider"], provider["provider"])}
    allowed = {"preset", "protocol", "endpoint", "model", "key_env", "key_store"}
    if set(provider) - allowed:
        raise ValueError("model API settings take preset, protocol, endpoint, model, and key_env or key_store")
    preset = presets.api_preset(provider.get("preset", "custom"))
    if preset is None:
        names = ", ".join(item["id"] for item in presets.load()["api"])
        raise ValueError(f"preset must be one of {names}")
    protocol = provider.get("protocol") or preset["protocol"]
    if protocol not in presets.PROTOCOLS:
        raise ValueError(f"protocol must be one of {', '.join(presets.PROTOCOLS)}")
    if preset["protocol"] and protocol != preset["protocol"]:
        raise ValueError(f"The {preset['name']} preset uses {preset['protocol']}; choose Custom for another protocol")
    endpoint = presets.check_endpoint(provider.get("endpoint") or preset["endpoint"])
    key_env, key_store = provider.get("key_env"), provider.get("key_store")
    if (key_env is None) == (key_store is None):
        raise ValueError("Say where the key is: key_env (an environment variable), key_store: keyring, "
                         "or key_store: none for a local server")
    if key_env is not None and (not isinstance(key_env, str) or not _ENV_NAME.fullmatch(key_env)):
        raise ValueError("key_env is the name of an environment variable, such as ANTHROPIC_API_KEY")
    if key_store is not None and key_store not in {"keyring", "none"}:
        raise ValueError("key_store is keyring or none")
    if key_store == "none" and preset["key"] == "required":
        raise ValueError(f"{preset['name']} needs an API key")
    return {"preset": preset["id"], "protocol": protocol, "endpoint": endpoint,
            "model": _check_model(provider.get("model")),
            **({"key_env": key_env} if key_env else {"key_store": key_store})}


def _runtime_settings(provider: dict) -> dict:
    """An agent app connection: a runtime preset, its command for a custom ACP agent, and any acknowledgement."""
    if "bridge" in provider and "runtime" not in provider:  # the v0.3.0 form
        provider = {"runtime": "codex" if provider["bridge"] == "codex" else provider["bridge"],
                    **{key: value for key, value in provider.items() if key != "bridge"}}
    if set(provider) - {"runtime", "command", "acknowledgement"}:
        raise ValueError("agent app settings take runtime, and command or acknowledgement where needed")
    preset = presets.runtime_preset(provider.get("runtime"))
    if preset is None:
        names = ", ".join(item["id"] for item in presets.load()["runtimes"])
        raise ValueError(f"runtime must be one of {names}")
    settings = {"runtime": preset["id"], "profile": preset["profile"]}
    command = provider.get("command")
    if preset["command"] is None:
        if not isinstance(command, list) or not 1 <= len(command) <= MAX_COMMAND_ARGS or not all(
                isinstance(item, str) and 1 <= len(item) <= 500 and "\x00" not in item for item in command):
            raise ValueError(f"command is the agent app and its arguments, as a list of up to {MAX_COMMAND_ARGS} strings")
        settings["command"] = command
    elif command is not None:
        raise ValueError(f"The {preset['name']} preset sets its own command")
    if preset.get("acknowledgement"):
        if provider.get("acknowledgement") != preset["acknowledgement"]:
            raise ValueError("Read and accept the acknowledgement for this agent app first")
        settings["acknowledgement_sha256"] = _digest(preset["acknowledgement"])
        settings["acknowledged_utc"] = _now()
    elif provider.get("acknowledgement") is not None:
        raise ValueError("This agent app has no acknowledgement to accept")
    return settings


def _migrate(item: dict) -> dict:
    """Read a v0.3.0 connection in the v0.3.1 form; the file is rewritten on its next write."""
    if item.get("kind") == "direct" and "provider" in item and "protocol" not in item:
        preset = presets.api_preset(LEGACY_PROVIDERS.get(item["provider"])) or {}
        item = {key: value for key, value in item.items() if key != "provider"}
        item.update(preset=preset.get("id", "custom"), protocol=preset.get("protocol"),
                    endpoint=preset.get("endpoint"))
    if item.get("kind") == "bridge" and "bridge" in item and "runtime" not in item:
        item = {key: value for key, value in item.items() if key != "bridge"}
        item.update(runtime="codex", profile="codex-exec")
    return item


def _public(connection: dict) -> dict:
    return {key: value for key, value in connection.items()
            if key not in {"token_sha256", "session_token_sha256"}}


class AgentSettings:
    # One lock for every instance: read-modify-write of agents.json must not
    # interleave, or a revocation could be overwritten by a concurrent write.
    _lock = threading.RLock()

    def __init__(self, workspace) -> None:
        self.path = workspace.root / SETTINGS_FILE

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)

    def _listed(self, data: dict) -> list[dict]:
        listed = data.get("connections")
        return [_migrate(item) for item in listed if isinstance(item, dict)] if isinstance(listed, list) else []

    # The switch ---------------------------------------------------------------

    def enabled(self) -> bool:
        # Anything unexpected in the file reads as off.
        data = self._read()
        return data.get("enabled") is True and data.get("notice_version") == NOTICE_VERSION

    def status(self) -> dict:
        data = self._read()
        accepted = data.get("notice_version") if isinstance(data.get("notice_version"), int) else None
        return {"enabled": self.enabled(), "notice_version": NOTICE_VERSION,
                "accepted_version": accepted, "accepted_utc": data.get("accepted_utc"),
                "needs_new_acceptance": accepted is not None and accepted != NOTICE_VERSION,
                "confirmation": CONFIRMATION, "notice": notice_text(),
                "paused": data.get("paused") is True,
                "tiers": list(TIERS), "default_tiers": list(DEFAULT_TIERS),
                "default_budgets": dict(DEFAULT_BUDGETS), "connections": self.connections()}

    def enable(self, confirmation: object, notice_version: object) -> dict:
        with self._lock:
            return self._enable_unlocked(confirmation=confirmation, notice_version=notice_version)

    def _enable_unlocked(self, confirmation: object, notice_version: object) -> dict:
        """Turn agent features on; the user must type the confirmation for this notice."""
        if notice_version != NOTICE_VERSION:
            raise ValueError("The notice has changed; read the current notice and accept it again")
        if not isinstance(confirmation, str) or " ".join(confirmation.split()).casefold() != CONFIRMATION.casefold():
            raise ValueError(f'Type "{CONFIRMATION}" to turn on agent features')
        data = self._read()
        self._write({**data, "enabled": True, "notice_version": NOTICE_VERSION, "accepted_utc": _now()})
        return self.status()

    def disable(self) -> dict:
        with self._lock:
            return self._disable_unlocked()

    def _disable_unlocked(self) -> dict:
        data = self._read()
        self._write({**data, "enabled": False, "disabled_utc": _now()})
        return self.status()

    def set_paused(self, paused: bool) -> dict:
        with self._lock:
            return self._set_paused_unlocked(paused=paused)

    def _set_paused_unlocked(self, paused: bool) -> dict:
        """Pause or resume every connection at once, without turning features off."""
        data = self._read()
        self._write({**data, "paused": bool(paused)})
        return self.status()

    def require_enabled(self) -> None:
        if not self.enabled():
            raise AgentsDisabled("Agent features are off. Turn them on in Quantarium under Agents, "
                                 "or run: quantarium agents enable")

    # Connections -----------------------------------------------------------------

    def connections(self) -> list[dict]:
        """Connections without their token digests, newest first."""
        return [_public(item) for item in reversed(self._listed(self._read()))]

    def create_connection(self, name: object, tiers: object = None, budgets: object = None,
                          edit_user_scripts: object = False, kind: object = "external",
                          provider: object = None) -> dict:
        with self._lock:
            return self._create_connection_unlocked(name=name, tiers=tiers, budgets=budgets,
                                                    edit_user_scripts=edit_user_scripts, kind=kind,
                                                    provider=provider)

    def _create_connection_unlocked(self, name: object, tiers: object = None, budgets: object = None,
                                    edit_user_scripts: object = False, kind: object = "external",
                                    provider: object = None) -> dict:
        """Add a connection. An external connection's token is returned only here, once."""
        self.require_enabled()
        settings = self._provider_settings(kind, provider)
        limits = dict(DEFAULT_BUDGETS)
        if budgets is not None:
            if not isinstance(budgets, dict) or not set(budgets) <= set(DEFAULT_BUDGETS):
                raise ValueError(f"budgets may set {', '.join(DEFAULT_BUDGETS)}")
            for key, value in budgets.items():
                if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_BUDGET:
                    raise ValueError(f"{key} must be a whole number from 1 to {MAX_BUDGET}")
                limits[key] = value
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 60:
            raise ValueError("Give the connection a name of 1 to 60 characters")
        name = name.strip()
        chosen = list(DEFAULT_TIERS) if tiers is None else tiers
        if not isinstance(chosen, list) or not all(isinstance(tier, str) for tier in chosen) \
                or not set(chosen) <= set(TIERS):
            raise ValueError(f"tiers must be a list drawn from {', '.join(TIERS)}")
        data = self._read()
        listed = self._listed(data)
        active = [item for item in listed if not item.get("revoked_utc")]
        if len(active) >= MAX_CONNECTIONS:
            raise ValueError(f"At most {MAX_CONNECTIONS} active connections; revoke one first")
        if any(str(item.get("name", "")).casefold() == name.casefold() for item in active):
            raise ValueError(f"A connection named {name} already exists")
        # Assistant connections act through Quantarium itself, so they have no token.
        token = "qtm_" + secrets.token_urlsafe(32) if kind == "external" else None
        connection = {"id": "a_" + secrets.token_hex(5), "name": name, "kind": kind,
                      "tiers": [tier for tier in TIERS if tier in chosen], "budgets": limits,
                      "edit_user_scripts": edit_user_scripts is True, **settings,
                      "token_sha256": _digest(token) if token else None, "created_utc": _now(),
                      "revoked_utc": None}
        self._write({**data, "connections": listed + [connection]})
        return {"connection": _public(connection), "token": token}

    @staticmethod
    def _provider_settings(kind: object, provider: object) -> dict:
        """Validate how an assistant connection reaches its model; never a key itself."""
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        if kind == "external":
            return {}
        if not isinstance(provider, dict):
            raise ValueError("An assistant connection needs provider settings")
        if kind == "bridge":
            return _runtime_settings(provider)
        return _api_settings(provider)

    def set_model(self, connection_id: object, model: object) -> dict:
        with self._lock:
            data = self._read()
            listed = self._listed(data)
            match = next((item for item in listed if item.get("id") == connection_id
                          and item.get("kind") == "direct" and not item.get("revoked_utc")), None)
            if match is None:
                raise ValueError(f"Unknown model API connection: {connection_id}")
            match["model"] = _check_model(model)
            # A cutoff belongs to one model; a new model needs its own.
            match.pop("training_cutoff", None)
            self._write({**data, "connections": listed})
            return _public(match)

    def set_daily_cost_limit(self, connection_id: object, usd: object) -> dict:
        """Limit a model API connection's estimated spending per UTC day across all cells, or clear it with None."""
        if usd is not None:
            if isinstance(usd, bool) or not isinstance(usd, (int, float)) or not math.isfinite(usd) or not 0 < usd <= 100_000:
                raise ValueError("usd must be a number of dollars above 0 and at most 100000, or null")
            usd = float(usd)
        with self._lock:
            data = self._read()
            listed = self._listed(data)
            match = next((item for item in listed if item.get("id") == connection_id
                          and item.get("kind") == "direct" and not item.get("revoked_utc")), None)
            if match is None:
                raise ValueError(f"Unknown model API connection: {connection_id}")
            if usd is None:
                match.pop("daily_cost_limit_usd", None)
            else:
                match["daily_cost_limit_usd"] = usd
            self._write({**data, "connections": listed})
            return _public(match)

    def set_training_cutoff(self, connection_id: object, cutoff: object) -> dict:
        """Record the model's training cutoff as its provider discloses it, or clear it with None."""
        if cutoff is not None:
            try:
                cutoff = date.fromisoformat(str(cutoff)).isoformat()
            except ValueError as exc:
                raise ValueError("training_cutoff must be an ISO date, such as 2026-03-31") from exc
        with self._lock:
            data = self._read()
            listed = self._listed(data)
            match = next((item for item in listed if item.get("id") == connection_id
                          and item.get("kind") == "direct" and not item.get("revoked_utc")), None)
            if match is None:
                raise ValueError(f"Unknown model API connection: {connection_id}")
            if cutoff is None:
                match.pop("training_cutoff", None)
            else:
                match["training_cutoff"] = cutoff
            self._write({**data, "connections": listed})
            return _public(match)

    def revoke_connection(self, connection_id: object) -> dict:
        with self._lock:
            return self._revoke_connection_unlocked(connection_id=connection_id)

    def _revoke_connection_unlocked(self, connection_id: object) -> dict:
        data = self._read()
        listed = self._listed(data)
        match = next((item for item in listed if item.get("id") == connection_id), None)
        if match is None:
            raise ValueError(f"Unknown connection: {connection_id}")
        match["revoked_utc"] = match.get("revoked_utc") or _now()
        self._write({**data, "connections": listed})
        return _public(match)

    def connection_for(self, token: object) -> dict:
        """The active connection a bearer token belongs to."""
        self.require_enabled()
        if not isinstance(token, str) or not token:
            raise AgentUnauthorized("Missing agent token")
        data = self._read()
        if data.get("paused") is True:
            raise AgentsPaused("All agent connections are paused. Resume them on the Agents page.")
        digest = _digest(token)
        now = datetime.now(timezone.utc).isoformat()
        for item in self._listed(data):
            stored = item.get("token_sha256")
            if isinstance(stored, str) and hmac.compare_digest(stored, digest):
                if item.get("revoked_utc"):
                    break
                return item
            # A bridge's per-turn token, valid until the turn ends or it expires.
            session = item.get("session_token_sha256")
            if (isinstance(session, str) and hmac.compare_digest(session, digest)
                    and not item.get("revoked_utc") and (item.get("session_expires_utc") or "") > now):
                return item
        raise AgentUnauthorized("Unknown or revoked agent token")

    def issue_session_token(self, connection_id: str) -> str:
        with self._lock:
            return self._issue_session_token_unlocked(connection_id=connection_id)

    def _issue_session_token_unlocked(self, connection_id: str) -> str:
        """A token for one bridge turn; only its digest is kept, and it expires."""
        token = "qtm_" + secrets.token_urlsafe(32)
        data = self._read()
        listed = self._listed(data)
        for item in listed:
            if item.get("id") == connection_id and item.get("kind") == "bridge" and not item.get("revoked_utc"):
                item["session_token_sha256"] = _digest(token)
                item["session_expires_utc"] = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
                self._write({**data, "connections": listed})
                return token
        raise AgentUnauthorized("Unknown or revoked bridge connection")

    def end_session_token(self, connection_id: str) -> None:
        with self._lock:
            return self._end_session_token_unlocked(connection_id=connection_id)

    def _end_session_token_unlocked(self, connection_id: str) -> None:
        data = self._read()
        listed = self._listed(data)
        for item in listed:
            if item.get("id") == connection_id:
                item.pop("session_token_sha256", None)
                item.pop("session_expires_utc", None)
        self._write({**data, "connections": listed})

    def connection_by_id(self, connection_id: object) -> dict:
        """An active connection the built-in assistant or a bridge acts as."""
        self.require_enabled()
        data = self._read()
        if data.get("paused") is True:
            raise AgentsPaused("All agent connections are paused. Resume them on the Agents page.")
        for item in self._listed(data):
            if item.get("id") == connection_id and not item.get("revoked_utc"):
                return item
        raise AgentUnauthorized("Unknown or revoked connection")

    @staticmethod
    def authorize(connection: dict, kind: str, name: str) -> str:
        """Refuse unless the connection's grant covers this operation; return its tier."""
        tier = OPERATIONS.get(kind, {}).get(name)
        if tier is None and kind == "get":
            tier = next((value for prefix, value in PREFIX_ROUTES.items()
                         if name.startswith(prefix)), None)
        if tier is None:
            raise AgentError(f"Agents may not use {kind} {name}")
        tiers = connection.get("tiers")
        if not isinstance(tiers, list) or tier not in tiers:
            raise AgentError(f"{kind} {name} needs the {tier} tier, which this connection lacks")
        return tier

    @staticmethod
    def check_params(connection: dict, kind: str, name: str, params: dict,
                     scenario_folders: tuple[str, ...] = ("scenarios",)) -> None:
        """Rules that depend on what a request touches, not only on its operation."""
        routed = name.split("/", 1)[1] if kind == "get" and "/" in name else None
        spec = params.get("spec") if kind == "command" and name in {"cell-create", "cell-save"} else None
        # Paths inside a cell spec: its model card, its sleeves' scripts, and decision files.
        nested = [spec.get("model_card"),
                  (spec.get("allocator") or {}).get("script") if isinstance(spec.get("allocator"), dict) else None,
                  *(sleeve.get(key) for sleeve in spec.get("sleeves") or []
                    if isinstance(sleeve, dict) for key in ("script", "decisions"))] \
            if isinstance(spec, dict) and isinstance(spec.get("sleeves") or [], list) else []
        # A script's source is code, not a path.
        keys = [key for key in PATH_PARAMS if not (key == "source" and name in {"script-write", "script-check"})]
        for value in [routed, *(params.get(key) for key in keys), *nested,
                      *(params.get("scenarios") or [] if isinstance(params.get("scenarios"), list) else [])]:
            if isinstance(value, str) and any(part.startswith(".") and part not in {"."}
                                              for part in value.replace("\\", "/").split("/")):
                raise AgentError("Agents cannot use paths in hidden folders or with ..")
        if kind == "job" and name == "deploy-suite" and params.get("name"):
            raise AgentError("Agents cannot name suites; Quantarium names them so no config is replaced")
        if kind == "job" and name in {"scenario-generate", "scenario-transform", "scenario-slice"}:
            output = params.get("output")
            parts = output.split("/") if isinstance(output, str) else []
            inside = any(output.startswith(folder.rstrip("/") + "/") for folder in scenario_folders) \
                if isinstance(output, str) else False
            if not inside or any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
                raise AgentError(f"Agents write scenarios only inside {', '.join(scenario_folders)}")
            if params.get("overwrite") and "manage" not in (connection.get("tiers") or []):
                raise AgentError("Replacing an existing scenario needs the manage tier")
        if kind == "command" and name == "script-write":
            key = params.get("key")
            if connection.get("edit_user_scripts") is not True and not (
                    isinstance(key, str) and key.startswith(AGENT_SCRIPT_FOLDER)
                    and ".." not in key.split("/")):
                raise AgentError(f"This connection may write scripts only under {AGENT_SCRIPT_FOLDER}")

    @staticmethod
    def check_budget(connection: dict, kind: str, name: str, log) -> None:
        """Refuse when this request would exceed the connection's budget."""
        budgets = {**DEFAULT_BUDGETS, **(connection.get("budgets") or {})}
        if kind == "job":
            used = log.count(connection["id"], tuple(OPERATIONS["job"]), "job", timedelta(hours=1))
            if used >= budgets["jobs_per_hour"]:
                raise BudgetExceeded(f"This connection has used its {budgets['jobs_per_hour']} jobs "
                                     "for the past hour; try again later")
        if kind == "command" and name == "proposal-create":
            used = log.count(connection["id"], ("proposal-create",), "command", timedelta(days=1))
            if used >= budgets["proposals_per_day"]:
                raise BudgetExceeded(f"This connection has filed its {budgets['proposals_per_day']} proposals "
                                     "for the past day; try again tomorrow")
        if kind == "command" and name in {"decision-inbox-create", "decision-submit"}:
            used = log.count(connection["id"], ("decision-inbox-create", "decision-submit"), "command",
                             timedelta(days=1))
            if used >= budgets["decisions_per_day"]:
                raise BudgetExceeded(f"This connection has submitted its {budgets['decisions_per_day']} decisions "
                                     "for the past day; try again tomorrow")
        if kind == "command" and name in {"cell-create", "cell-duplicate"}:
            used = log.count(connection["id"], ("cell-create", "cell-duplicate"), "command",
                             timedelta(days=1))
            if used >= budgets["cells_per_day"]:
                raise BudgetExceeded(f"This connection has created its {budgets['cells_per_day']} cells "
                                     "for the past day; try again tomorrow")


def run_cli(action: str, workspace, *, input_fn=input, print_fn=print, interactive: bool | None = None) -> int:
    """``quantarium agents status|enable|disable``; returns the exit status."""
    import sys

    settings = AgentSettings(workspace)
    if action == "status":
        status = settings.status()
        state = "on" if status["enabled"] else "off"
        print_fn(f"Agent features are {state} in {workspace.root}.")
        if status["needs_new_acceptance"]:
            print_fn("The notice has changed since you accepted it; run: quantarium agents enable")
        active = [item for item in status["connections"] if not item["revoked_utc"]]
        for item in active:
            print_fn(f"  {item['name']} ({item['id']}): {', '.join(item['tiers'])}")
        return 0
    if action == "disable":
        settings.disable()
        print_fn("Agent features are off.")
        return 0
    # Enabling needs a person at a terminal: a piped or scripted confirmation,
    # which an agent could produce, is refused.
    if not (sys.stdin.isatty() if interactive is None else interactive):
        print_fn("quantarium agents enable must be run in an interactive terminal, "
                 "or turn agent features on in the Quantarium app.")
        return 2
    print_fn(notice_text())
    answer = input_fn(f'Type "{CONFIRMATION}" to turn on agent features: ')
    try:
        settings.enable(answer, NOTICE_VERSION)
    except ValueError as exc:
        print_fn(str(exc))
        return 1
    print_fn("Agent features are on. Turn them off any time with: quantarium agents disable")
    return 0
