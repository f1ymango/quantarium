"""Proposals: consequential actions an agent asks the user to approve.

An agent with the propose tier files a proposal: the action, its
parameters, a rationale, and evidence. The proposal pins what it was made
against (a fingerprint of each cell's settings and the hash of each script
those cells use). Only the browser app can approve or reject, and approval
is refused if anything pinned has changed since. Approving runs the action
as the user, through the normal job and command paths.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from datetime import datetime, timezone

from .errors import QuantariumError, NotFound
from .scenario import load_scenario

STORE = ".quantarium/proposals.json"
MAX_RATIONALE = 4000
MAX_EVIDENCE = 20
# Actions an agent may propose, and whether each runs as a job or a command.
ACTIONS = {"deploy-feed": "job", "feed-batch": "job", "retire-cell": "job",
           "cell-archive": "command", "cell-restore": "command",
           "lab-register": "command",
           # A suite on the holdout scenarios, run once per cell version.
           "holdout-test": "job"}
STATES = ("pending", "approved", "rejected", "stale", "failed")


def spec_fingerprint(record: dict) -> str:
    """Identifies a cell variant by its settings, whatever it is named."""
    return hashlib.sha256(json.dumps(record["spec"], sort_keys=True).encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cell_ids(action: str, params: dict) -> list[str]:
    ids = params.get("cells") if action in {"deploy-feed", "holdout-test", "lab-register"} else params.get("ids")
    return [item for item in ids if isinstance(item, str)] if isinstance(ids, list) else []


class Proposals:
    def __init__(self, workspace, library) -> None:
        self.workspace = workspace
        self.library = library
        self.path = workspace.root / STORE
        self._lock = threading.Lock()

    def _read(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    def _write(self, items: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(items, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)

    def pins(self, action: str, params: dict) -> dict:
        """Fingerprints of the cells and scripts a proposal acts on."""
        cells, scripts, models = {}, {}, {}
        for cell_id in _cell_ids(action, params):
            record = self.library.get(cell_id)
            cells[cell_id] = {"name": record["name"], "version": record["version"],
                              "fingerprint": hashlib.sha256(json.dumps(
                                  [record["name"], record["spec"], record["archived"]],
                                  sort_keys=True).encode()).hexdigest()}
            allocator = record["spec"].get("allocator") or {}
            for sleeve in [*record["spec"].get("sleeves", []), allocator]:
                key = sleeve.get("script") if isinstance(sleeve, dict) else None
                if not isinstance(key, str):
                    continue
                path = self.workspace.resolve(key)
                scripts[key] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            model_key = record["spec"].get("model_card")
            if isinstance(model_key, str) and model_key:
                path = self.workspace.resolve(model_key)
                models[model_key] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        scenarios = {}
        if action == "lab-register":
            for key in params.get("scenarios", []):
                scenario = load_scenario(self.workspace.resolve(key))
                scenarios[key] = {"manifest_sha256": scenario.manifest_sha256,
                                  "prices_sha256": hashlib.sha256(scenario.prices_path.read_bytes()).hexdigest(),
                                  "actions_sha256": hashlib.sha256(scenario.actions_path.read_bytes()).hexdigest()
                                  if scenario.actions_path else None}
        return {"cells": cells, "scripts": scripts,
                **({"models": models} if models else {}),
                **({"scenarios": scenarios} if action == "lab-register" else {})}

    def create(self, connection: dict, action: object, params: object, rationale: object,
               evidence: object = None, variants_tested: int = 0) -> dict:
        if action not in ACTIONS:
            raise QuantariumError(f"Agents may propose only: {', '.join(ACTIONS)}", "invalid_parameter")
        if not isinstance(params, dict):
            raise QuantariumError("params must be an object", "invalid_parameter")
        if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > MAX_RATIONALE:
            raise QuantariumError(f"Explain the proposal in a rationale of 1 to {MAX_RATIONALE} characters",
                               "invalid_parameter")
        evidence = [] if evidence is None else evidence
        if not isinstance(evidence, list) or len(evidence) > MAX_EVIDENCE or \
                not all(isinstance(item, str) and len(item) <= 500 for item in evidence):
            raise QuantariumError(f"evidence is a list of at most {MAX_EVIDENCE} short strings, such as job ids",
                               "invalid_parameter")
        proposal = {"id": "p_" + secrets.token_hex(5), "action": action, "params": params,
                    "rationale": rationale.strip(), "evidence": evidence,
                    "connection_id": connection.get("id"), "connection_name": connection.get("name"),
                    "pins": self.pins(action, params), "variants_tested": variants_tested,
                    "status": "pending", "created_utc": _now(),
                    "decided_utc": None, "note": None, "job_id": None, "error": None}
        with self._lock:
            self._write(self._read() + [proposal])
        return proposal

    def list(self, connection_id: str | None = None, status: str | None = None) -> list[dict]:
        items = [item for item in reversed(self._read())
                 if (connection_id is None or item.get("connection_id") == connection_id)
                 and (status is None or item.get("status") == status)]
        return items

    def get(self, proposal_id: object) -> dict:
        match = next((item for item in self._read() if item.get("id") == proposal_id), None)
        if match is None:
            raise NotFound(f"Unknown proposal: {proposal_id}", "proposal_not_found")
        return match

    def _update(self, proposal_id: str, **fields) -> dict:
        with self._lock:
            items = self._read()
            for item in items:
                if item.get("id") == proposal_id:
                    item.update(fields)
                    self._write(items)
                    return item
        raise NotFound(f"Unknown proposal: {proposal_id}", "proposal_not_found")

    def decide(self, proposal_id: object, decision: object, note: object, run) -> dict:
        """Approve or reject a pending proposal. ``run(proposal)`` performs it as the user."""
        if decision not in {"approve", "reject"}:
            raise QuantariumError("decision must be approve or reject", "invalid_parameter")
        if note is not None and (not isinstance(note, str) or len(note) > MAX_RATIONALE):
            raise QuantariumError("note must be a short string", "invalid_parameter")
        proposal = self.get(proposal_id)
        if proposal["status"] != "pending":
            raise QuantariumError(f"This proposal is already {proposal['status']}", "proposal_decided")
        if decision == "reject":
            return self._update(proposal["id"], status="rejected", decided_utc=_now(), note=note)
        try:
            current = self.pins(proposal["action"], proposal["params"])
        except (FileNotFoundError, PermissionError, ValueError):
            current = None
        if current != proposal["pins"]:
            self._update(proposal["id"], status="stale", decided_utc=_now(), note=note)
            raise QuantariumError("The cells or scripts this proposal names changed after it was made",
                               "proposal_stale", "Ask the agent to propose again")
        try:
            result = run(proposal)
        except Exception as exc:
            self._update(proposal["id"], status="failed", decided_utc=_now(), note=note,
                         error=f"{type(exc).__name__}: {exc}")
            raise
        job_id = result.get("id") if ACTIONS[proposal["action"]] == "job" and isinstance(result, dict) else None
        return self._update(proposal["id"], status="approved", decided_utc=_now(), note=note, job_id=job_id)
