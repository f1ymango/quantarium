"""Version 1 of Quantarium's local JSON API, independent of the HTTP layer.

``Api.get`` answers read requests; ``Api.submit`` queues an action that
writes, such as running a suite. Actions run one at a time on a single worker
thread, so two jobs never write the same database concurrently. Every path is
a workspace-relative key and is refused if it resolves outside the workspace.
Running a config executes its strategy code, which is trusted local Python.
"""

from __future__ import annotations

import contextvars
import copy
import hashlib
import json
import threading
import time
import traceback
import uuid
from collections import OrderedDict
from datetime import date, datetime, timezone
from pathlib import Path
from queue import Queue
from typing import Any, Callable

from . import __version__
from .analysis import read_analysis
from .config import _finite_nonnegative, cost_model, load_config, load_feed_config, strategy_root
from .data import load_daily_csv
from .engine import run_static
from .feed import provider_for, run_feed_batch
from .journal import read_report
from .lifecycle import retire_cell
from .scenario import generate_scenario, load_scenario, slice_scenario, transform_scenario
from .suite import load_suite, run_suite, suite_report
from .summary import page_curve, summarize_analysis, summarize_suite
from .timeline import read_timeline
from .trades import write_decisions
from .userdata import import_user_data, read_user_data
from .cells import CellLibrary
from .deploy import Deployer, arena, cell_result
from .holdings import read_holdings
from .agent_log import AgentLog
from .agents import AgentError, AgentSettings, HoldoutRefused
from . import inbox as decision_inbox, provenance
from .deciders import listing as deciders_listing
from .errors import QuantariumError, describe
from .onboarding import Onboarding
from .assistant import Assistant
from .holdout import Holdout
from .lab import Lab
from . import providers
from .proposals import ACTIONS as PROPOSAL_ACTIONS, Proposals, spec_fingerprint
from .script_files import ScriptFiles, check_source
from .scripts import strategy_classes
from .workspace import Workspace

KINDS = ("static", "feed", "live")
# The agent connection making the current request, per thread, so concurrent
# requests never see each other's caller.
CURRENT_AGENT: contextvars.ContextVar[dict | None] = contextvars.ContextVar("quantarium_agent", default=None)
MAX_JOBS = 200


class ApiError(Exception):
    """A request the client can fix; ``status`` is the HTTP status to return."""

    def __init__(self, status: int, message: str, code: str | None = None,
                 hint: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.hint = hint


def _required(params: dict, name: str) -> str:
    value = params.get(name)
    if not isinstance(value, str) or not value:
        raise ApiError(400, f"Missing parameter: {name}", "missing_parameter", f"Add the {name} parameter")
    return value


def _flag(params: dict, name: str) -> bool:
    return str(params.get(name, "")).lower() in {"1", "true", "yes"}


def _kind(params: dict) -> str:
    kind = _required(params, "kind")
    if kind not in KINDS:
        raise ApiError(400, f"kind must be one of {', '.join(KINDS)}", "invalid_parameter")
    return kind


class Api:
    def __init__(self, workspace: Workspace, *, tutorial: bool = False) -> None:
        self.workspace = workspace
        self.cells = CellLibrary(workspace)
        self.deployer = Deployer(workspace, self.cells)
        self.scripts = ScriptFiles(workspace)
        # The tutorial's own Api serves its private workspace and has no tutorial.
        self.onboarding = None if tutorial else Onboarding(
            workspace, self.cells, lambda root: Api(root, tutorial=True))
        # The agent switch belongs to the real workspace, not the tutorial's.
        self.agents = None if tutorial else AgentSettings(workspace)
        self.agent_log = None if tutorial else AgentLog(workspace)
        self.proposals = None if tutorial else Proposals(workspace, self.cells)
        self.holdout = None if tutorial else Holdout(workspace)
        self.lab = Lab(workspace, self.cells, self.holdout)
        # The assistant's model providers; not Feed's market-data provider_for.
        self.assistant = None if tutorial else Assistant(self, providers.provider_for)
        self._cell_lock = threading.Lock()
        self.jobs: OrderedDict[str, dict] = OrderedDict()
        # Idempotency key -> (job id, request fingerprint), for jobs still listed.
        self._idempotency: dict[str, tuple[str, str]] = {}
        self._lock = threading.Lock()
        self._queue: Queue[tuple[str, Callable[[], Any]] | None] = Queue()
        self._worker = threading.Thread(target=self._work, name="quantarium-jobs", daemon=True)
        self._worker.start()

    def _scope(self) -> "Api":
        """The Api for the current state: the tutorial's while it runs, else this one."""
        if self.onboarding is not None and self.onboarding.active():
            return self.onboarding.tutorial_api()
        return self

    def _chat_command(self, name: str, params: dict) -> Any:
        assistant = self.assistant
        if name == "chat-create":
            return assistant.create(params.get("connection_id"))
        if name == "chat-send":
            return assistant.send(params.get("id"), params.get("text"), params.get("page"))
        if name == "chat-stop":
            return assistant.stop(params.get("id"))
        if name == "chat-delete":
            if assistant.busy(params.get("id")):
                raise ApiError(400, "Stop the assistant before deleting this chat", "chat_busy")
            # "codex_transcript" is the v0.3.0 name of the option.
            return assistant.chats.delete(params.get("id"), params.get("app_transcript") is True
                                          or params.get("codex_transcript") is True)
        if name == "chat-rename":
            title = params.get("title")
            if not isinstance(title, str) or not title.strip():
                raise ApiError(400, "title must be a nonempty string", "invalid_parameter")
            chat = assistant.chats.get(params.get("id"))
            chat["title"] = title.strip()[:80]
            return assistant.chats.save(chat)
        raise ApiError(404, f"Unknown command: {name}", "unknown_command")

    def _fingerprints(self, cell_ids: object) -> dict[str, str]:
        return {cell_id: spec_fingerprint(self.cells.get(cell_id))
                for cell_id in (cell_ids if isinstance(cell_ids, list) else []) if isinstance(cell_id, str)}

    # Holdout scenarios, as agents see them ----------------------------------

    def _holdout_paths(self, kind: str, name: str, params: dict) -> list[str]:
        values = [params.get(key) for key in ("source", "data", "output", "key", "actions")]
        if isinstance(params.get("scenarios"), list):
            values += params["scenarios"]
        if kind == "get" and name.startswith("scripts/"):
            values.append(name.split("/", 1)[1])
        return [value for value in values if isinstance(value, str)]

    def _suite_uses_holdout(self, config_key: str) -> bool:
        try:
            spec, _ = load_suite(self.workspace.resolve(config_key))
        except (OSError, ValueError, KeyError, PermissionError):
            return False
        manifests = self.holdout.manifests()
        return any(item.scenario.manifest_sha256 in manifests for item in spec.scenarios)

    def _feed_uses_holdout(self, config_key: str) -> bool:
        try:
            raw = json.loads(self.workspace.resolve(config_key).read_text(encoding="utf-8"))
        except (OSError, ValueError, PermissionError):
            return False
        # Feed configs name scenario_path or data_path; Static configs name data_path.
        source = raw.get("scenario_path") or raw.get("data_path")
        base = self.workspace.resolve(config_key).parent
        return bool(source) and self.holdout.covers(base / source)

    def _lab_uses_holdout(self, record: dict) -> bool:
        if self.holdout is None or not self.holdout.keys():
            return False
        manifests = self.holdout.manifests()
        return any(pin["manifest_sha256"] in manifests
                   or self.holdout.covers(self.workspace.resolve(pin["key"]))
                   for pin in record["spec"]["scenarios"])

    def _holdout_guard(self, kind: str, name: str, params: dict) -> None:
        """Refuse agent requests that would read, run on, or reveal holdout scenarios."""
        if self.holdout is None or not self.holdout.keys():
            return
        if kind == "get" and name.startswith("lab/") and \
                self._lab_uses_holdout(self.lab.get(name.split("/", 1)[1])):
            raise HoldoutRefused("That Lab experiment includes a holdout scenario")
        for value in self._holdout_paths(kind, name, params):
            if self.holdout.covers(self.workspace.resolve(value)):
                raise HoldoutRefused(f"{value} is a holdout scenario, kept out of agents' sight")
        config = params.get("config")
        if isinstance(config, str) and (name in {"suite-report", "run-suite"} and self._suite_uses_holdout(config)
                                        or name in {"feed-batch", "run-static", "config"}
                                        and self._feed_uses_holdout(config)):
            raise HoldoutRefused("That run includes holdout scenarios, kept out of agents' sight")
        if name in {"analysis", "report", "timeline", "holdings"} and isinstance(params.get("db"), str):
            database = self.workspace.resolve(params["db"])
            if params.get("kind") in {None, "static"} and self.holdout.run_is_holdout(database, params.get("id")):
                raise HoldoutRefused("That run used a holdout scenario, kept out of agents' sight")
            if params.get("kind") == "feed" and self._experiment_uses_holdout(params["db"], params.get("id")):
                raise HoldoutRefused("That experiment is fed by a holdout scenario, kept out of agents' sight")

    def _experiment_uses_holdout(self, db_key: str, experiment_id: object) -> bool:
        for item in arena(self.workspace, self.cells):
            if item.get("db") == db_key and (experiment_id is None or item.get("id") == experiment_id) \
                    and item.get("config") and self._feed_uses_holdout(item["config"]):
                return True
        return False

    def _deployment_hidden(self, deployment: dict) -> bool:
        """Whether a cell's deployment reveals holdout results to an agent."""
        if CURRENT_AGENT.get() is None or self.holdout is None or not self.holdout.keys():
            return False
        if deployment["kind"] == "suite":
            return self._suite_uses_holdout(deployment["config"])
        if deployment["kind"] == "feed":
            return self._feed_uses_holdout(deployment["config"])
        return self.holdout.run_is_holdout(self.workspace.resolve(deployment["db"]), deployment.get("id"))

    def _agent_view(self, connection: dict, name: str, params: dict, result: Any) -> Any:
        """What an agent may see of a read: its own jobs, no holdout scenarios, and its variant count."""
        if name.startswith("jobs/") and isinstance(result, dict) \
                and result.get("agent_connection") != connection["id"]:
            # Someone else's job (the user's, or one an approval started): status only.
            result = {key: result.get(key) for key in ("id", "action", "status", "submitted_utc", "finished_utc")}
            result["note"] = "Not this connection's job, so its parameters and results are not shown"
        if self.holdout is not None and self.holdout.keys():
            if name == "workspace":
                keys = set(self.holdout.keys())
                result = {**result, "scenarios": [item for item in result["scenarios"]
                                                  if item.get("key") not in keys]}
            if name == "arena":
                result = [item for item in result
                          if not (item.get("config") and self._feed_uses_holdout(item["config"]))]
            if name == "lab":
                result = [item for item in result if not self._lab_uses_holdout(item)]
        if name in {"analysis", "suite-report"} and str(params.get("summary", "")).lower() in {"1", "true", "yes"}:
            tested = self.agent_log.variants(connection["id"])
            result = {**result, "variants_tested": tested}
            if tested >= 20:
                result["caveats"] = list(result.get("caveats", [])) + [
                    f"This connection has simulated {tested} cell variants. The best of many tries usually "
                    "does worse going forward; check it with a Feed test or a holdout test."]
        return result

    def _propose(self, params: dict) -> dict:
        """File a proposal for the calling agent, after checking it could run."""
        agent = CURRENT_AGENT.get()
        if agent is None:
            raise ApiError(403, "Proposals are filed by agents; act directly instead", "not_permitted")
        action, inner = params.get("action"), params.get("params")
        if action not in PROPOSAL_ACTIONS:
            raise ApiError(400, f"Agents may propose only: {', '.join(PROPOSAL_ACTIONS)}", "invalid_parameter")
        if not isinstance(inner, dict):
            raise ApiError(400, "params must be an object", "invalid_parameter")
        # The same path and parameter rules as acting directly, before anyone sees it.
        self.agents.check_params(agent, PROPOSAL_ACTIONS[action], action, inner,
                                 tuple(self.workspace.folders["scenarios"]))
        # Only a holdout test may touch holdout scenarios, and it names none itself.
        self._holdout_guard(PROPOSAL_ACTIONS[action], action, inner)
        if action == "holdout-test":
            if not self.holdout.keys():
                raise ApiError(400, "The user has not marked any holdout scenarios", "no_holdout")
            ids = inner.get("cells")
            if not isinstance(ids, list) or not ids or set(inner) - {"cells"}:
                raise ApiError(400, "A holdout test takes only cells, a nonempty list of cell ids",
                               "invalid_parameter")
            for cell_id, fingerprint in self._fingerprints(ids).items():
                problems = self.cells.check(self.cells.get(cell_id))
                if problems:
                    raise ApiError(400, f"{cell_id} is not ready: {'; '.join(problems)}", "cell_not_ready")
                if self.holdout.tested(fingerprint):
                    raise ApiError(400, f"{cell_id} has had its holdout test; change the cell to test again",
                                   "holdout_used")
        elif PROPOSAL_ACTIONS[action] == "job":
            self._action(action, inner)
        elif action == "lab-register":
            self.lab.validate(inner)
        else:
            ids = inner.get("ids")
            if not isinstance(ids, list) or not ids:
                raise ApiError(400, "ids must be a nonempty list of cell ids", "invalid_parameter")
        return self.proposals.create(agent, action, inner, params.get("rationale"), params.get("evidence"),
                                     self.agent_log.variants(agent["id"]))

    def _run_proposal(self, proposal: dict) -> Any:
        """Carry out an approved proposal as the user."""
        if proposal["action"] == "deploy-feed":
            # Approving a forward test reviews the exact script versions it showed.
            for key, digest in proposal["pins"]["scripts"].items():
                if digest and any(entry["sha256"] == digest and entry["state"] != "reviewed"
                                  for entry in provenance.entries(self.workspace.root)):
                    provenance.mark_reviewed(self.workspace.root, digest)
        if proposal["action"] == "holdout-test":
            cells = proposal["params"]["cells"]
            if any(self.holdout.tested(spec_fingerprint(self.cells.get(cell_id))) for cell_id in cells):
                raise ApiError(400, "A cell in this proposal has had its holdout test", "holdout_used")
            job = self.submit("deploy-suite", {"cells": cells, "scenarios": self.holdout.keys()})
            self.holdout.record_test(self._fingerprints(cells), proposal["id"])
            return job
        if PROPOSAL_ACTIONS[proposal["action"]] == "job":
            return self.submit(proposal["action"], proposal["params"])
        return self.command(proposal["action"], proposal["params"])

    def _proposal_views(self, items: list[dict]) -> list[dict]:
        """Proposals with what the app shows: each pinned script's review state."""
        unreviewed = {entry["sha256"] for entry in provenance.entries(self.workspace.root)
                      if entry["state"] != "reviewed"}
        return [{**item, "scripts": [{"key": key, "sha256": digest, "agent_unreviewed": digest in unreviewed}
                                     for key, digest in item["pins"]["scripts"].items()]}
                for item in items]

    @staticmethod
    def _as_agent(connection: dict, call: Callable[[], Any]) -> Any:
        """Run a command with the calling connection known, for script provenance."""
        marker = CURRENT_AGENT.set(connection)
        try:
            return call()
        finally:
            CURRENT_AGENT.reset(marker)

    def agent_request(self, token: str, kind: str, name: str, params: dict,
                      idempotency_key: str | None = None) -> Any:
        """Serve a request made with an agent token, after the switch and grant checks.

        This is how an agent token reaches the API. The checks run before any
        handler, so a refused request has no effect.
        """
        return self._agent_serve(lambda: self.agents.connection_for(token), kind, name, params,
                                 idempotency_key)

    def agent_call(self, connection_id: str, kind: str, name: str, params: dict,
                   idempotency_key: str | None = None) -> Any:
        """The same path for the built-in assistant, which acts as a connection without a token."""
        return self._agent_serve(lambda: self.agents.connection_by_id(connection_id), kind, name, params,
                                 idempotency_key)

    def _agent_serve(self, resolve: Callable[[], dict], kind: str, name: str, params: dict,
                     idempotency_key: str | None) -> Any:
        if self.agents is None:
            raise ApiError(404, "Agent requests go to the workspace's own server")
        started = time.monotonic()
        connection = None
        entry = {"kind": kind, "name": name, "params": params}

        def record(outcome: str, status: int, code=None, message=None, job_id=None) -> None:
            self.agent_log.record(connection=connection, outcome=outcome, status=status, code=code,
                                  message=message, job_id=job_id,
                                  duration_ms=round((time.monotonic() - started) * 1000), **entry)

        try:
            connection = resolve()
            if self.onboarding is not None and self.onboarding.active():
                # The tutorial is a guided state for the user, and finishing it
                # copies files without their script provenance.
                raise AgentError("The tutorial is running; agents can act again when it ends")
            self.agents.authorize(connection, kind, name)
            self.agents.check_params(connection, kind, name, params,
                                     tuple(self.workspace.folders["scenarios"]))
            self.agents.check_budget(connection, kind, name, self.agent_log)
            self._holdout_guard(kind, name, params)
            if kind == "get" and name == "agent-log":
                result = self.agent_log.entries(connection["id"], int(params.get("limit") or 100))
            elif kind == "get" and name == "proposals":
                result = self.proposals.list(connection["id"], params.get("status") or None)
            elif kind == "get":
                result = self._as_agent(connection, lambda: self.get(name, params))
                result = self._agent_view(connection, name, params, result)
            elif kind == "command":
                result = self._as_agent(connection, lambda: self.command(name, params))
            else:
                result = self._as_agent(connection, lambda: self.submit(name, params, idempotency_key))
                if name in {"deploy-suite", "deploy-static"}:
                    self.agent_log.record_variants(connection["id"], self._fingerprints(params.get("cells")))
        except AgentError as exc:
            record("refused", exc.status, exc.code, str(exc))
            raise
        except ApiError as exc:
            record("error", exc.status, exc.code, str(exc))
            raise
        except Exception as exc:
            record("error", 400 if isinstance(exc, (ValueError, KeyError, TypeError)) else 500,
                   None, f"{type(exc).__name__}: {exc}")
            raise
        record("ok", 202 if kind == "job" else 200,
               job_id=result.get("id") if kind == "job" and isinstance(result, dict) else None)
        return result

    # Reads -----------------------------------------------------------------

    def get(self, route: str, params: dict) -> Any:
        if route == "onboarding" and self.onboarding is not None:
            return self.onboarding.state()
        if route == "agents" and self.agents is not None:
            from . import presets

            return {**self.agents.status(), "assistant": providers.capabilities(), "presets": presets.load(),
                    "prices": presets.prices()}
        if route == "workspace" and self.holdout is not None and not self.onboarding.active():
            overview = self.workspace.overview()
            keys = set(self.holdout.keys())
            for item in overview["scenarios"]:
                item["holdout"] = item.get("key") in keys
            return overview
        if route == "agents/chats" and self.assistant is not None:
            return self.assistant.chats.list()
        if route.startswith("agents/chats/") and self.assistant is not None:
            from .runtimes import state_profile, transcripts

            chat_id = route.split("/", 2)[2]
            chat = self.assistant.chats.get(chat_id)
            state = chat.get("bridge") or {}
            return {**chat, "running": self.assistant.busy(chat_id), "app_profile": state_profile(state),
                    "app_transcripts": [str(path) for path in transcripts(state)]}
        if route == "agents/proposals" and self.proposals is not None:
            return self._proposal_views(self.proposals.list(status=params.get("status") or None))
        if route == "agents/scripts" and self.agents is not None:
            return provenance.entries(self.workspace.root)
        if route == "agents/log" and self.agent_log is not None:
            return self.agent_log.entries(params.get("connection") or None,
                                          int(params.get("limit") or 100))
        scope = self._scope()
        if scope is not self:
            return scope.get(route, params)
        handlers = {
            "health": lambda: {"status": "ok", "version": __version__, "api": "v1"},
            "workspace": self.workspace.overview,
            "analysis": lambda: self._analysis(params),
            "timeline": lambda: self._timeline(params),
            "report": lambda: read_report(self._file(params, "db"), params.get("id")),
            "config": lambda: self._config(params),
            "scenario": lambda: self._scenario(params),
            "suite-report": lambda: self._suite_report(params),
            "jobs": self._job_list,
            "cells": lambda: self._cell_list(params),
            "scripts": self._scripts,
            "holdings": lambda: self._holdings(params),
            "arena": lambda: arena(self.workspace, self.cells),
            "supercells": self._supercells,
            "decision-inboxes": lambda: decision_inbox.inboxes(self.workspace.root),
            # Browser only: deciders are the user's programs, not among the routes agents may read.
            "deciders": lambda: deciders_listing(self.workspace.root),
            "lab": self.lab.list,
        }
        if route.startswith("jobs/"):
            job_id = route.split("/", 1)[1]
            wait = min(max(float(params.get("wait") or 0), 0), 120)
            if wait:
                # One request that returns when the job finishes, instead of polling.
                deadline = time.monotonic() + wait
                while self._job(job_id)["status"] in {"queued", "running"} and time.monotonic() < deadline:
                    time.sleep(0.1)
            return self._job(job_id)
        if route.startswith("cells/"):
            return self._cell_detail(route.split("/", 1)[1])
        if route.startswith("lab-report/"):
            # Browser only: the full record, as a page to download and share.
            from .evidence import render

            experiment_id = route.split("/", 1)[1]
            return {"filename": f"{experiment_id}-evidence.html", "html": render(self.lab.get(experiment_id), self.workspace.root)}
        if route.startswith("lab/"):
            return self._lab_record(route.split("/", 1)[1])
        if route.startswith("tapes/"):
            # Browser only: tapes are not among the routes agents may read.
            from .ask import tape_entries

            record = self.cells.get(route.split("/", 1)[1])
            grant = record["spec"].get("ask") or {}
            return {"cell": record["id"], "tape": grant.get("tape"), "model": grant.get("model"),
                    "entries": tape_entries(self.workspace.root, grant.get("tape"))}
        if route.startswith("scripts/"):
            return self.scripts.read(route.split("/", 1)[1])
        if route not in handlers:
            raise ApiError(404, f"Unknown endpoint: {route}", "unknown_endpoint",
                           "See docs/api.md for the endpoints")
        return handlers[route]()

    def _file(self, params: dict, name: str) -> Path:
        path = self.workspace.resolve(_required(params, name))
        if not path.is_file():
            raise ApiError(404, f"Not found: {params[name]}", "not_found",
                           "GET workspace lists the files Quantarium can see")
        return path

    def _analysis(self, params: dict) -> dict:
        path = self._file(params, "db")
        analysis = read_analysis(path, _kind(params), params.get("id"),
                                 benchmark=params.get("benchmark") or None)
        if _flag(params, "summary"):
            return summarize_analysis(analysis, path)
        if "curve_offset" in params or "curve_limit" in params:
            return page_curve(analysis, params.get("curve_offset"), params.get("curve_limit"))
        return analysis

    def _suite_report(self, params: dict) -> dict:
        report = suite_report(load_suite(self._file(params, "config"))[0])
        return summarize_suite(report) if _flag(params, "summary") else report

    def _timeline(self, params: dict) -> dict:
        kind = _kind(params)
        parse = ((lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")))
                 if kind == "live" else date.fromisoformat)
        bounds = {name: parse(params[key]) if params.get(key) else None
                  for name, key in (("start", "from"), ("through", "through"))}
        return read_timeline(self._file(params, "db"), kind, _required(params, "cell"),
                             params.get("id"), **bounds)

    def _config(self, params: dict) -> dict:
        path = self._file(params, "key")
        if path.suffix != ".json":
            raise ApiError(400, "Only JSON configs can be read")
        return {"key": self.workspace.key(path), "config": json.loads(path.read_text(encoding="utf-8"))}

    def _scenario(self, params: dict) -> dict:
        """A scenario's summary and its closing prices, for charting its shape."""
        directory = self.workspace.resolve(_required(params, "key"))
        scenario = load_scenario(directory)
        # Imported research data lists and delists symbols; a day without a bar shows as null.
        days, tape, _ = load_daily_csv(scenario.prices_path, set(scenario.symbols),
                                       every_session=scenario.survivorship is None)
        return {"key": self.workspace.key(directory), **scenario.summary(),
                "provenance": scenario.provenance,
                "closes": {symbol: [[day.isoformat(), tape[day][symbol].close if symbol in tape[day] else None]
                                    for day in days]
                           for symbol in scenario.symbols}}

    def _card(self, record: dict, scope: str | None = None, lab_record: dict | None = None) -> dict:
        """A cell's card; ``scope`` limits its headline result to Static or Feed runs."""
        problems = self.cells.check(record)
        kinds = {"static": {"static", "suite"}, "feed": {"feed"}}.get(scope)
        if CURRENT_AGENT.get() is not None:
            record = {**record, "deployments": [item for item in record["deployments"]
                                                if not self._deployment_hidden(item)]}
        chosen = [item for item in record["deployments"] if kinds is None or item["kind"] in kinds]
        result = cell_result(self.workspace, record, chosen[-1]) if chosen else None
        spec = record["spec"]
        status = ("draft" if problems and not record["deployments"] else
                  "failed" if result and result.get("status") == "failed" else
                  "results" if result else "configured")
        return {"id": record["id"], "name": record["name"], "mode": spec["mode"],
                "version": record["version"], "archived": record["archived"],
                "updated_utc": record["updated_utc"], "holdings": len(spec["symbols"]),
                "sleeves": len(spec["sleeves"]), "status": status, "problems": problems,
                "deployments": {kind: sum(item["kind"] == kind for item in record["deployments"])
                                for kind in ("static", "suite", "feed")},
                "model_card": spec.get("model_card"),
                "asks": (spec.get("ask") or {}).get("model"),
                "allocator": (spec.get("allocator") or {}).get("class") if isinstance(spec.get("allocator"), dict) else None,
                "lab": ({"status": lab_record["result"]["verdict"]["status"]
                         if lab_record.get("result") else lab_record["status"],
                         "attempt": lab_record["attempt_number"],
                         "scenarios": len(lab_record["spec"]["scenarios"])}
                        if lab_record else None),
                "result": result}

    def _lab_record(self, experiment_id: str) -> dict:
        """One experiment; while it runs, how many questions its candidate has asked so far."""
        record = self.lab.get(experiment_id)
        grant = record["spec"]["candidate"].get("ask")
        if record.get("status") == "running" and grant and CURRENT_AGENT.get() is None:
            from .ask import tape_summary

            summary = tape_summary(self.workspace.root, grant.get("tape"))
            record = {**record, "model_calls": summary["answers"] + summary["failed"]}
        return record

    def _grant_ask(self, params: dict) -> dict:
        """Give one of a cell's scripts model access; the server pins the script's exact bytes."""
        from .ask import validate_settings

        record = self.cells.get(_required(params, "id"))
        spec = copy.deepcopy(record["spec"])
        script = params.get("script")
        allocator = spec.get("allocator") if isinstance(spec.get("allocator"), dict) else {}
        scripts = {sleeve.get("script") for sleeve in spec.get("sleeves") or []} | {allocator.get("script")}
        if not isinstance(script, str) or script not in scripts - {None}:
            raise ApiError(400, "script must be one of this cell's scripts", "invalid_parameter")
        connection = self.agents.connection_by_id(params.get("connection_id"))
        if connection.get("kind") != "direct":
            raise ApiError(400, "Model access uses a model API connection", "invalid_parameter")
        previous = spec.get("ask") or {}
        # Prices the user gives win; otherwise the dated table's, and a model it does not list needs them given.
        if "input_usd_per_million" not in params or "output_usd_per_million" not in params:
            from .presets import price_for

            listed = price_for(connection.get("preset"), connection.get("model"))
            if listed is None:
                raise ApiError(400, f"Quantarium's price table does not list {connection.get('model')}; enter its prices "
                               "per million input and output tokens", "missing_parameter")
            params = {"input_usd_per_million": listed["input_usd_per_million"],
                      "output_usd_per_million": listed["output_usd_per_million"], **params}
        grant = {"connection_id": connection["id"], "model": connection["model"],
                 "workspace": str(self.workspace.root), "tape": previous.get("tape") or f".quantarium/tapes/{record['id']}.jsonl",
                 "mode": params.get("mode", "record"),
                 "approved_script_sha256": hashlib.sha256(self.workspace.resolve(script).read_bytes()).hexdigest(),
                 **{key: params.get(key, default) for key, default in (
                     ("max_calls", 300), ("max_tokens", 300_000), ("max_output_tokens", 2000),
                     ("max_estimated_cost_usd", 1.0), ("input_usd_per_million", 0.0),
                     ("output_usd_per_million", 0.0), ("min_sessions_between_calls", 1))}}
        try:
            validate_settings(grant)
        except ValueError as exc:
            raise ApiError(400, str(exc), "invalid_parameter") from None
        spec["ask"] = grant
        return self.cells.save(record["id"], spec=spec)

    def _supercells(self) -> list[dict]:
        """Every model-driven cell, its parts, and the evidence of its latest Lab experiment.

        Browser only: it reads the cells' answer tapes, which live with Quantarium's own files.
        """
        from .ask import tape_summary

        connections = {item["id"]: item for item in self.agents.connections()} if self.agents else {}
        experiments = self.lab.list()
        result = []
        for record in self.cells.all():
            spec = record["spec"]
            parts = {}
            if spec.get("model_card"):
                parts["risk_card"] = {"key": spec["model_card"], "information_end": spec.get("information_end")}
            grant = spec.get("ask")
            if isinstance(grant, dict):
                connection = connections.get(grant.get("connection_id"), {})
                parts["ask"] = {"model": grant.get("model"), "mode": grant.get("mode"),
                                "connection_name": connection.get("name"),
                                "connection_active": bool(connection) and not connection.get("revoked_utc"),
                                "training_cutoff": connection.get("training_cutoff"),
                                "max_calls": grant.get("max_calls"), "max_tokens": grant.get("max_tokens"),
                                "max_estimated_cost_usd": grant.get("max_estimated_cost_usd"),
                                "tape": grant.get("tape"),
                                **tape_summary(self.workspace.root, grant.get("tape"))}
            allocator = spec.get("allocator")
            if isinstance(allocator, dict):
                parts["allocator"] = {"class": allocator.get("class"), "builtin": bool(allocator.get("builtin")),
                                      "script": allocator.get("script")}
            if not parts:
                continue
            mine = [item for item in experiments if item["spec"].get("candidate_id") == record["id"]]
            current = [item for item in mine if item["spec"].get("candidate_version") == record["version"]]
            latest = (current or mine or [None])[0]
            result.append({"id": record["id"], "name": record["name"], "version": record["version"],
                           "mode": spec.get("mode"), "archived": record["archived"],
                           "problems": self.cells.check(record), "parts": parts,
                           "experiments": len(mine),
                           "evidence": None if latest is None else {
                               **_evidence(latest), "for_version": latest["spec"].get("candidate_version"),
                               "current_version": latest["spec"].get("candidate_version") == record["version"]}})
        return result

    def _cell_list(self, params: dict) -> list[dict]:
        archived = params.get("archived") == "1"
        scope = params.get("scope") or None
        if scope not in {None, "static", "feed"}:
            raise ApiError(400, "scope must be static or feed")
        latest_lab = {}
        for experiment in self.lab.list():
            if CURRENT_AGENT.get() is not None and self._lab_uses_holdout(experiment):
                continue
            spec = experiment["spec"]
            latest_lab.setdefault((spec["candidate_id"], spec["candidate_version"]), experiment)
        return [self._card(record, scope, latest_lab.get((record["id"], record["version"])))
                for record in self.cells.all()
                if record["archived"] == archived]

    def _cell_detail(self, cell_id: str) -> dict:
        record = self.cells.get(cell_id)
        if CURRENT_AGENT.get() is not None:
            record = {**record, "deployments": [item for item in record["deployments"]
                                                if not self._deployment_hidden(item)]}
        return {**record, "card": self._card(record),
                "results": [cell_result(self.workspace, record, deployment)
                            for deployment in record["deployments"]]}

    def _scripts(self) -> list[dict]:
        """Scripts in scanned folders, plus any a library cell already uses."""
        listed = self.workspace.scripts()
        known = {item["key"] for item in listed}
        for record in self.cells.all():
            for sleeve in record["spec"].get("sleeves", []):
                key = sleeve.get("script")
                if not key or key in known:
                    continue
                try:
                    path = self.workspace.resolve(key)
                except PermissionError:
                    continue
                classes = strategy_classes(path) if path.is_file() else []
                if classes:
                    listed.append({"key": key, "module": path.stem, "classes": classes})
                    known.add(key)
        # The registry lives in the real workspace, also during the tutorial.
        root = next((folder for folder in [self.workspace.root, *self.workspace.root.parents]
                     if (folder / provenance.REGISTRY).is_file()), None)
        for item in listed:
            path = self.workspace.resolve(item["key"])
            item["agent_unreviewed"] = bool(root) and path.is_file() and \
                provenance.is_unreviewed(root, path.read_bytes(), item["key"])
        return listed

    def _holdings(self, params: dict) -> dict:
        return read_holdings(self._file(params, "db"), _kind(params), params.get("id"),
                             _required(params, "cell"), start=params.get("from"),
                             through=params.get("through"))

    # Commands: quick, synchronous changes to the cell library ----------------

    def command(self, name: str, params: dict) -> Any:
        if self.onboarding is not None:
            if name == "onboarding-start":
                return self.onboarding.start()
            if name == "onboarding-step":
                return self.onboarding.step(params.get("step"), params.get("cell_id"))
            if name == "onboarding-finish":
                return self.onboarding.finish(_required(params, "status"))
        if self.agents is not None:
            # Only the browser app reaches commands today. When agent tokens
            # arrive, these two must stay refused for them.
            if name == "agents-enable":
                return self.agents.enable(params.get("confirmation"), params.get("notice_version"))
            if name == "agents-disable":
                return self.agents.disable()
            if name == "agent-connection-create":
                return self.agents.create_connection(params.get("name"), params.get("tiers"),
                                                    params.get("budgets"),
                                                    params.get("edit_user_scripts", False),
                                                    params.get("kind", "external"), params.get("provider"))
            if name == "agent-connection-revoke":
                revoked = self.agents.revoke_connection(params.get("id"))
                providers.forget_key(revoked)
                return revoked
            if name == "agent-connection-key":
                # The key goes straight to the system keychain; it is never
                # saved by Quantarium, logged, or returned.
                if CURRENT_AGENT.get() is not None:
                    raise ApiError(403, "Only the user stores keys", "not_permitted")
                return providers.store_key(self.agents.connection_by_id(params.get("id")), params.get("key"))
            if name in {"agent-connection-test", "agent-connection-models", "agent-connection-model",
                        "agent-connection-cutoff", "agent-connection-daily-limit"}:
                # These send the user's key to the endpoint, or change the connection.
                if CURRENT_AGENT.get() is not None:
                    raise ApiError(403, "Only the user manages connections", "not_permitted")
                if name == "agent-connection-model":
                    return self.agents.set_model(params.get("id"), params.get("model"))
                if name == "agent-connection-cutoff":
                    return self.agents.set_training_cutoff(params.get("id"), params.get("training_cutoff"))
                if name == "agent-connection-daily-limit":
                    return self.agents.set_daily_cost_limit(params.get("id"), params.get("usd"))
                connection = self.agents.connection_by_id(params.get("id"))
                if connection.get("kind") != "direct":
                    raise ApiError(400, "Only model API connections can be tested this way", "invalid_parameter")
                if name == "agent-connection-test":
                    return providers.check_connection(connection)
                return providers.list_models(connection)
            if name in {"agents-pause", "agents-resume"}:
                return self.agents.set_paused(name == "agents-pause")
            if name == "script-review":
                return provenance.mark_reviewed(self.workspace.root, params.get("sha256"))
            if name.startswith("chat-"):
                if CURRENT_AGENT.get() is not None:
                    raise ApiError(403, "Agents cannot drive the assistant", "not_permitted")
                return self._chat_command(name, params)
            if name == "scenario-holdout":
                if CURRENT_AGENT.get() is not None:
                    raise ApiError(403, "Only the user chooses holdout scenarios", "not_permitted")
                return {"holdout": self.holdout.set(params.get("key"), bool(params.get("holdout")))}
            if name == "proposal-create":
                return self._propose(params)
            if name == "proposal-decide":
                if CURRENT_AGENT.get() is not None:
                    raise ApiError(403, "Only the user decides proposals", "not_permitted")
                return self.proposals.decide(params.get("id"), params.get("decision"), params.get("note"),
                                             self._run_proposal)
        scope = self._scope()
        if scope is not self:
            return scope.command(name, params)
        if name == "lab-register":
            if CURRENT_AGENT.get() is not None:
                raise ApiError(403, "Agents propose Lab registrations for your approval", "not_permitted")
            return self.lab.register(params)
        ids = params.get("ids") or ([params["id"]] if params.get("id") else [])
        if name in {"data-check", "trades-convert"}:
            # Outside data comes in only by the user's hand; a survivorship declaration is the user's word.
            if CURRENT_AGENT.get() is not None:
                raise ApiError(403, "Only the user brings in outside data", "not_permitted")
            if name == "data-check":
                folder = self.workspace.resolve(_required(params, "folder"))
                return {"folder": self.workspace.key(folder), **read_user_data(folder).summary()}
            trades = self._file(params, "trades")
            output = self.workspace.resolve(params.get("output") or
                                            f"{self.workspace.folders['decisions'][0]}/{trades.stem}.json")
            prices = self._file(params, "prices") if params.get("prices") else None
            try:
                report = write_decisions(trades, output, prices=prices,
                                         system=str(params.get("system") or "trades-csv"),
                                         overwrite=bool(params.get("overwrite")))
            except FileExistsError:
                # A decision file may be pinned by a Lab registration; replacing it is a deliberate choice.
                raise ApiError(409, f"{self.workspace.key(output)} already exists", "already_exists",
                               "Rename or remove it first, or pass overwrite") from None
            return {**report, "output": self.workspace.key(output)}
        if name in {"decider-create", "decider-delete"}:
            if CURRENT_AGENT.get() is not None:
                raise ApiError(403, "Only the user registers deciders", "not_permitted")
            from . import deciders

            if name == "decider-create":
                return deciders.create(self.workspace.root, params)
            return deciders.delete(self.workspace.root, params.get("name"))
        if name in {"decision-inbox-create", "decision-submit"}:
            agent = CURRENT_AGENT.get()
            by = f"agent:{agent['id']}" if agent else "user"
            if name == "decision-inbox-create":
                return decision_inbox.create(self.workspace.root, params.get("name"), params.get("symbols"), by)
            return decision_inbox.submit(self.workspace.root, params.get("inbox"), params, by)
        if name == "script-write":
            with self._cell_lock:
                return self.scripts.write(params.get("key"), params.get("source"),
                                          params.get("base_sha256"), agent=CURRENT_AGENT.get())
        if name == "script-check":
            if params.get("key") is not None:
                return self.scripts.read(params["key"])["check"]
            source = params.get("source")
            if not isinstance(source, str):
                raise ApiError(400, "Give a script key or its source", "missing_parameter")
            return check_source(source)
        with self._cell_lock:
            if name == "cell-create":
                return self.cells.create(params.get("name"))
            if name == "cell-ask-grant":
                if CURRENT_AGENT.get() is not None:
                    raise ApiError(403, "Only the user can grant context.ask access", "not_permitted")
                return self._grant_ask(params)
            if name == "cell-save":
                if params.get("spec") is not None and not isinstance(params["spec"], dict):
                    raise ApiError(400, "spec must be an object", "invalid_parameter")
                if CURRENT_AGENT.get() is not None and any(
                        isinstance(sleeve, dict) and "decider" in sleeve
                        for sleeve in (params.get("spec") or {}).get("sleeves") or []
                        if isinstance((params.get("spec") or {}).get("sleeves"), list)):
                    raise ApiError(403, "Only the user chooses a decider for a cell", "not_permitted")
                if CURRENT_AGENT.get() is not None and ((params.get("spec") or {}).get("ask") is not None
                                                        or self.cells.get(_required(params, "id"))["spec"].get("ask") is not None):
                    raise ApiError(403, "Only the user can grant or edit context.ask access", "not_permitted")
                return self.cells.save(_required(params, "id"), name=params.get("name"),
                                       spec=params.get("spec"))
            if name == "cell-duplicate" and CURRENT_AGENT.get() is not None and \
                    self.cells.get(_required(params, "id"))["spec"].get("ask") is not None:
                raise ApiError(403, "Only the user can copy a cell that may ask a model", "not_permitted")
            if name == "cell-duplicate":
                return [self.cells.duplicate(cell_id) for cell_id in ids]
            if name in {"cell-archive", "cell-restore"}:
                return self.cells.set_archived(ids, name == "cell-archive")
            if name == "cell-delete":
                records = [self.cells.get(cell_id) for cell_id in ids]
                blocked = [record["name"] for record in records if record["deployments"]]
                if blocked:
                    raise ApiError(400, f"Cells with results cannot be deleted: {', '.join(blocked)}",
                                   "cell_has_results", "Archive them instead")
                for record in records:
                    self.cells.delete(record["id"])
                return {"deleted": ids}
        raise ApiError(404, f"Unknown command: {name}", "unknown_command",
                       "See docs/api.md for the commands")

    # Actions ---------------------------------------------------------------

    def submit(self, action: str, params: dict, idempotency_key: str | None = None) -> dict:
        """Queue a job. Resubmitting with the same idempotency key returns the first job."""
        scope = self._scope()
        if scope is not self:
            return scope.submit(action, params, idempotency_key)
        fingerprint = None
        if idempotency_key is not None:
            if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 128:
                raise ApiError(400, "idempotency_key must be a string of 1 to 128 characters",
                               "invalid_parameter")
            fingerprint = json.dumps([action, params], sort_keys=True, default=str)
            with self._lock:
                seen = self._idempotency.get(idempotency_key)
                if seen and seen[0] in self.jobs:
                    if seen[1] != fingerprint:
                        raise ApiError(409, "This idempotency_key was used for a different request",
                                       "idempotency_conflict", "Use a new key for a new request")
                    return dict(self.jobs[seen[0]])
        runner = self._action(action, params)
        job_id = uuid.uuid4().hex
        agent = CURRENT_AGENT.get()
        job = {"id": job_id, "action": action, "params": params, "status": "queued",
               "submitted_utc": _now(), "started_utc": None, "finished_utc": None,
               "result": None, "error": None,
               # Which agent connection submitted it, so agents read only their own results.
               "agent_connection": agent["id"] if agent else None}
        with self._lock:
            self.jobs[job_id] = job
            if idempotency_key is not None:
                self._idempotency[idempotency_key] = (job_id, fingerprint)
            while len(self.jobs) > MAX_JOBS:
                oldest = next(iter(self.jobs))
                if self.jobs[oldest]["status"] in {"queued", "running"}:
                    break
                del self.jobs[oldest]
            for key in [key for key, (listed, _) in self._idempotency.items() if listed not in self.jobs]:
                del self._idempotency[key]
        self._queue.put((job_id, runner))
        return dict(job)

    def _refuse_agent_deciders(self, params: dict) -> None:
        """Deciders are the user's own programs and may spend the user's money; agents never start one."""
        from .referee import DECIDER_ENTRYPOINT

        cells = params.get("cells") if isinstance(params.get("cells"), list) else []
        for cell_id in cells:
            if isinstance(cell_id, str):
                try:
                    sleeves = self.cells.get(cell_id)["spec"].get("sleeves") or []
                except (FileNotFoundError, ValueError, QuantariumError):
                    continue
                if any("decider" in sleeve for sleeve in sleeves):
                    raise ApiError(403, "Only the user runs cells that use a decider", "not_permitted")
        if isinstance(params.get("config"), str):
            try:
                text = self._file(params, "config").read_text(encoding="utf-8")
            except (OSError, ValueError, ApiError):
                return
            # Checked on the decoded JSON, not its text: an escaped spelling such as \u0044ecider
            # decodes to the same entrypoint, and the class may be reached under either package name.
            try:
                decoded = [json.loads(text)]
            except ValueError:
                decoded = [text]
            class_name = DECIDER_ENTRYPOINT.split(":", 1)[1]
            while decoded:
                value = decoded.pop()
                if isinstance(value, dict):
                    decoded.extend(value.keys())
                    decoded.extend(value.values())
                elif isinstance(value, list):
                    decoded.extend(value)
                elif isinstance(value, str) and class_name in value:
                    raise ApiError(403, "Only the user runs configs that use a decider", "not_permitted")

    def _action(self, action: str, params: dict) -> Callable[[], Any]:
        """Validate an action's parameters now, so a bad request fails at submit time."""
        if CURRENT_AGENT.get() is not None:
            self._refuse_agent_deciders(params)
        if action == "run-static":
            path = self._file(params, "config")
            spec, raw = load_config(path)
            return lambda: read_report(Path(spec.database_path),
                                       run_static(spec, raw, strategy_root(path, raw)))
        if action == "run-suite":
            path = self._file(params, "config")
            spec, raw = load_suite(path)
            return lambda: {**run_suite(spec, raw, strategy_root(path, raw)),
                            "report": suite_report(spec)}
        if action == "lab-run":
            if CURRENT_AGENT.get() is not None:
                raise ApiError(403, "Only the user runs a registered Lab experiment", "not_permitted")
            experiment_id = _required(params, "id")
            self.lab.get(experiment_id)
            return lambda: self.lab.run(experiment_id)
        if action == "feed-batch":
            path = self._file(params, "config")
            spec, raw = load_feed_config(path)
            through = params.get("through")
            through_date = date.fromisoformat(through) if through else None
            return lambda: run_feed_batch(spec, raw, strategy_root(path, raw),
                                          provider_for(spec), through=through_date)
        if action in {"deploy-static", "deploy-suite", "deploy-feed"}:
            ids = params.get("cells")
            if not isinstance(ids, list) or not ids:
                raise ApiError(400, "cells must be a nonempty list of cell ids")
            for cell_id in ids:
                problems = self.cells.check(self.cells.get(cell_id))
                if problems:
                    raise ApiError(400, f"{cell_id} is not ready: {'; '.join(problems)}", "cell_not_ready",
                                   f"Fix these problems with cell-save; GET cells/{cell_id} lists them")
            # Checked here as well as in the config, so a bad cost is refused before any file is written.
            costs = {name: _finite_nonnegative(params.get(name, 0), name)
                     for name in ("slippage_bps", "commission_per_order")}
            # Per-share and minimum commissions and range slippage are written only when set.
            costs["extra_costs"] = {name: value for name, value in cost_model(params).as_dict().items() if value}
            if action == "deploy-static":
                data = _required(params, "data")
                self._file(params, "data")
                return lambda: self.deployer.static(ids, data, actions=params.get("actions"), **costs)
            if action == "deploy-suite":
                scenarios = params.get("scenarios")
                if not isinstance(scenarios, list) or not scenarios:
                    raise ApiError(400, "scenarios must be a nonempty list")
                return lambda: self.deployer.suite(
                    ids, scenarios, name=params.get("name"), benchmark=params.get("benchmark"),
                    symbol_maps=params.get("symbol_maps"), **costs)
            return lambda: self.deployer.feed(
                ids, feed_id=_required(params, "feed_id"), provider=params.get("provider"),
                source=params.get("source"), start_date=params.get("start_date"),
                through=params.get("through"), **costs)
        if action == "retire-cell":
            database = self._file(params, "db")
            kind = _required(params, "kind")
            if kind not in {"feed", "live"}:
                raise ApiError(400, "Only feed and live cells can be retired")
            feed_id, cell = _required(params, "id"), _required(params, "cell")
            return lambda: retire_cell(kind, database, feed_id, cell, params.get("reason"))
        if action == "scenario-generate":
            spec = params.get("spec")
            if not isinstance(spec, dict):
                raise ApiError(400, "spec must be an object")
            output = self.workspace.resolve(_required(params, "output"))
            return lambda: generate_scenario(spec, output,
                                             overwrite=bool(params.get("overwrite"))).summary()
        if action == "scenario-transform":
            source = self.workspace.resolve(_required(params, "source"))
            spec = params.get("spec")
            if not isinstance(spec, dict):
                raise ApiError(400, "spec must be an object")
            output = self.workspace.resolve(_required(params, "output"))
            return lambda: transform_scenario(source, spec, output,
                                              overwrite=bool(params.get("overwrite"))).summary()
        if action == "data-import":
            if CURRENT_AGENT.get() is not None:
                raise ApiError(403, "Only the user brings in outside data", "not_permitted")
            folder = self.workspace.resolve(_required(params, "folder"))
            name = _required(params, "name")
            output = self.workspace.resolve(params.get("output") or f"{self.workspace.folders['scenarios'][0]}/{name}")

            def imported() -> dict:
                scenario, warnings = import_user_data(
                    folder, output, name=name, description=str(params.get("description", "")),
                    tags=tuple(params.get("tags", [])), overwrite=bool(params.get("overwrite")))
                return {"key": self.workspace.key(output), **scenario.summary(), "warnings": warnings}
            return imported
        if action == "scenario-slice":
            source = self._file(params, "source")
            output = self.workspace.resolve(_required(params, "output"))
            start = date.fromisoformat(_required(params, "from"))
            through = date.fromisoformat(_required(params, "through"))
            tags = params.get("tags", [])
            actions = params.get("actions")
            actions_path = self._file(params, "actions") if actions else None
            return lambda: slice_scenario(
                source, output, start, through, name=_required(params, "name"),
                description=str(params.get("description", "")), tags=tuple(tags),
                actions=actions_path, overwrite=bool(params.get("overwrite")),
            ).summary()
        raise ApiError(404, f"Unknown action: {action}", "unknown_action",
                       "See docs/api.md for the job actions")

    def _work(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            job_id, runner = item
            self._update(job_id, status="running", started_utc=_now())
            try:
                result = runner()
            except Exception as exc:  # reported to the client through the job
                code, hint = describe(exc)
                self._update(job_id, status="failed", finished_utc=_now(),
                             error={"type": type(exc).__name__, "message": str(exc),
                                    "code": code or "job_failed", "hint": hint,
                                    "trace": traceback.format_exc(limit=8)})
            else:
                self._update(job_id, status="succeeded", finished_utc=_now(),
                             result=json.loads(json.dumps(result, default=str)))

    def _update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            self.jobs[job_id].update(fields)

    def _job(self, job_id: str) -> dict:
        if self.onboarding is not None and job_id not in self.jobs and self.onboarding.active():
            return self.onboarding.tutorial_api()._job(job_id)
        with self._lock:
            if job_id not in self.jobs:
                raise ApiError(404, f"Unknown job: {job_id}", "unknown_job",
                               "Jobs last until the server stops; GET jobs lists them")
            return dict(self.jobs[job_id])

    def _job_list(self) -> list[dict]:
        with self._lock:
            return [{key: job[key] for key in ("id", "action", "status", "submitted_utc",
                                               "finished_utc")}
                    for job in reversed(self.jobs.values())]

    def wait(self, job_id: str, timeout: float = 60) -> dict:
        """Block until a job finishes; for tests and scripted use."""
        deadline = datetime.now(timezone.utc).timestamp() + timeout
        while datetime.now(timezone.utc).timestamp() < deadline:
            job = self._job(job_id)
            if job["status"] in {"succeeded", "failed"}:
                return job
            threading.Event().wait(0.02)
        raise TimeoutError(job_id)

    def close(self, timeout: float | None = 5) -> bool:
        if self.onboarding is not None:
            self.onboarding.close()
        self._queue.put(None)
        self._worker.join(timeout=timeout)
        return not self._worker.is_alive()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _evidence(experiment: dict) -> dict:
    """A short account of one Lab experiment, for a supercell's card."""
    verdict = (experiment.get("result") or {}).get("verdict") or {}
    rows = verdict.get("rows") or []
    contributions: dict[str, list[float]] = {}
    for row in rows:
        for entry in row.get("ablations") or []:
            if entry.get("contribution_pp") is not None:
                contributions.setdefault(entry["part"], []).append(entry["contribution_pp"])
    gaps = [row["gap_pp"] for row in ((verdict.get("disguise") or {}).get("rows") or [])
            if row.get("gap_pp") is not None]
    return {"experiment_id": experiment["id"], "name": experiment["spec"].get("name"),
            "status": verdict.get("status") or experiment.get("status"),
            "run_status": experiment.get("status"), "attempt": experiment.get("attempt_number"),
            "scenarios": len(experiment["spec"].get("scenarios") or []),
            "model_contamination": (verdict.get("scope") or {}).get("model_contamination"),
            "contributions_pp": {part: sum(values) / len(values) for part, values in contributions.items()},
            "disguise_gap_pp": sum(gaps) / len(gaps) if gaps else None,
            "finished_utc": experiment.get("finished_utc")}

