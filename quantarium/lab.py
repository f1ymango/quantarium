"""Pre-registered, controlled daily experiments and their local evidence ledger."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

from .agents import AgentSettings
from .config import _cells, cost_model
from .errors import QuantariumError, NotFound
from .decisions import load_decisions
from .legacy import current_value
from .model import CostModel
from .referee import DECIDER_ENTRYPOINT, INBOX_ENTRYPOINT, pin_prefix
from .scenario import _NAME, disguise_scenario, load_scenario
from .suite import SuiteScenario, SuiteSpec, run_suite, suite_report
from .trials import DSR_THRESHOLD, PBO_MIN_TRIALS, daily_returns, deflated_sharpe, moments, overfitting_probability

STORE = ".quantarium/lab.json"
# A candidate this close to its best control, in percentage points, matches it.
MATCH_TOLERANCE_PP = 1e-6
DATABASE = "runs/lab.sqlite"
# Disguised copies of scenarios, kept with the ledger and out of agents' sight.
DISGUISE_FOLDER = ".quantarium/lab-disguised"
MAX_SCENARIOS = 20


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _nonnegative(value: object, label: str, *, maximum: float | None = None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite nonnegative number") from exc
    if not math.isfinite(number) or number < 0 or (maximum is not None and number > maximum):
        range_text = "finite and nonnegative" if maximum is None else f"finite and between 0 and {maximum}"
        raise ValueError(f"{label} must be {range_text}")
    return number


def _cell_names(candidate: dict) -> dict:
    """The names of the candidate's controls and ablation variants, without collisions."""
    taken = {candidate["name"]}

    def unique(label: str) -> str:
        name = label
        while name in taken:
            name += "_"
        taken.add(name)
        return name

    controls = {"BuyAndHold": unique("lab_buy_hold"), "EqualWeight": unique("lab_equal_weight")}
    ablations = {}
    if candidate.get("ask") is not None:
        ablations["ask"] = unique(f"{candidate['name']}__without_model")
    if candidate.get("model_card"):
        ablations["risk"] = unique(f"{candidate['name']}__without_risk_card")
    if candidate.get("allocator"):
        ablations["allocator"] = unique(f"{candidate['name']}__fixed_allocation")
    return {"controls": controls, "ablations": ablations}


def _renamed(value: object, names: dict[str, str]) -> object:
    """Strategy parameters with real symbols replaced by their disguised names."""
    if isinstance(value, str):
        return names.get(value.upper(), value)
    if isinstance(value, list):
        return [_renamed(item, names) for item in value]
    if isinstance(value, dict):
        return {key: _renamed(item, names) for key, item in value.items()}
    return value



def _data_names(pin: dict) -> set[str]:
    """What a scenario pin's data is known by: its family, and its exact prices.

    Matching the prices as well catches the same data under a new name or in
    a copied folder, whose manifest, and so whose family, differs.
    """
    names = {pin["family"]}
    if pin.get("prices_sha256"):
        names.add("prices:" + pin["prices_sha256"])
    return names

class Lab:
    """The local ledger. Registration freezes inputs before a run is queued."""

    _lock = threading.RLock()

    def __init__(self, workspace, library, holdout=None) -> None:
        self.workspace = workspace
        self.library = library
        self.holdout = holdout
        self.path = workspace.root / STORE

    def _read(self) -> list[dict]:
        if not self.path.is_file():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise ValueError("The Lab ledger is not a list of experiments")
        return data

    def _write(self, items: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(items, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)

    def list(self) -> list[dict]:
        return list(reversed(self._read()))

    def get(self, experiment_id: str) -> dict:
        found = next((item for item in self._read() if item.get("id") == experiment_id), None)
        if found is None:
            raise NotFound(f"Unknown Lab experiment: {experiment_id}", "lab_not_found")
        return found

    def _update(self, experiment_id: str, **fields) -> dict:
        with self._lock:
            items = self._read()
            for item in items:
                if item.get("id") == experiment_id:
                    item.update(fields)
                    self._write(items)
                    return item
        raise NotFound(f"Unknown Lab experiment: {experiment_id}", "lab_not_found")

    def _family(self, scenario, seen: set[str] | None = None) -> str:
        seen = set() if seen is None else seen
        if scenario.manifest_sha256 in seen:
            return "manifest:" + scenario.manifest_sha256
        seen.add(scenario.manifest_sha256)
        provenance = scenario.provenance
        if provenance.get("method") == "slice" and provenance.get("source_sha256"):
            return "source:" + provenance["source_sha256"]
        if provenance.get("method") == "import-data" and (provenance.get("source_files") or {}).get("prices.csv"):
            return "source:" + provenance["source_files"]["prices.csv"]
        parent_hash = provenance.get("parent_manifest_sha256")
        if parent_hash:
            for listed in self.workspace.scenarios():
                if listed.get("manifest_sha256") == parent_hash:
                    parent = load_scenario(self.workspace.resolve(listed["key"]))
                    return self._family(parent, seen)
            return "parent:" + parent_hash
        return "manifest:" + scenario.manifest_sha256

    def _lineage(self, scenario, seen: set[str] | None = None) -> str:
        """synthetic or historical by origin; unknown when a transformed scenario's parent is missing."""
        if scenario.kind in {"synthetic", "historical"}:
            return scenario.kind
        seen = set() if seen is None else seen
        parent_hash = scenario.provenance.get("parent_manifest_sha256")
        if not parent_hash or parent_hash in seen:
            return "unknown"
        seen.add(parent_hash)
        for listed in self.workspace.scenarios():
            if listed.get("manifest_sha256") == parent_hash:
                return self._lineage(load_scenario(self.workspace.resolve(listed["key"])), seen)
        return "unknown"

    def _model(self, entry: dict) -> dict | None:
        """The asking model and its training cutoff, as recorded on its connection."""
        grant = entry.get("ask")
        if grant is None:
            return None
        connection = next((item for item in AgentSettings(self.workspace).connections()
                           if item.get("id") == grant["connection_id"]), {})
        return {"connection_id": grant["connection_id"], "model": grant["model"],
                "training_cutoff": connection.get("training_cutoff"),
                "cutoff_source": "user_entered_from_provider" if connection.get("training_cutoff") else None}

    def _snapshot(self, params: dict) -> dict:
        idea, name = params.get("idea"), params.get("name")
        if not isinstance(idea, str) or not _NAME.fullmatch(idea):
            raise ValueError("idea must use 1-64 letters, digits, dashes, or underscores")
        idea = idea.casefold()
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise ValueError("name must be 1-100 characters")
        ids = params.get("cells")
        if not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], str):
            raise ValueError("A Lab experiment needs exactly one candidate cell")
        record = self.library.get(ids[0])
        problems = self.library.check(record)
        if problems:
            raise ValueError(f"{record['name']} is not ready: {'; '.join(problems)}")
        entry, root = self.library.engine_cell(record)
        script_hashes = {}
        for key in [sleeve.get("script") for sleeve in record["spec"]["sleeves"]] + \
                [(record["spec"].get("allocator") or {}).get("script")]:
            if key:
                script_hashes[key] = _hash(self.workspace.resolve(key))
        # A decider: an outside program run live, pinned, and judged like a model that may remember history.
        decider = None
        for strategy in entry["strategies"]:
            if strategy["entrypoint"] == DECIDER_ENTRYPOINT:
                from .deciders import fingerprint, get

                item = get(self.workspace.root, strategy["parameters"]["decider"])
                strategy["parameters"] = {**strategy["parameters"], "fingerprint": fingerprint(item)}
                decider = {"name": item["name"], "model": f"the decider {item['name']}",
                           "training_cutoff": item.get("training_cutoff"), "fingerprint": fingerprint(item)}
        # Forward decisions: frozen as they stand, and judged in full on real prices.
        inboxes = {}
        for strategy in entry["strategies"]:
            if strategy["entrypoint"] == INBOX_ENTRYPOINT:
                from .inbox import pin, read

                path = Path(strategy["parameters"]["inbox"])
                frozen = pin(path)
                current = read(path)
                if not current.decisions:
                    raise ValueError(f"The inbox {path.stem} has no on-time decisions yet")
                strategy["parameters"] = {**strategy["parameters"], "until_bytes": frozen["bytes"],
                                          "sha256": frozen["sha256"]}
                inboxes[path.stem] = {**frozen, "first_on_time": current.first_on_time.isoformat(),
                                      "last_on_time": max(current.decisions).isoformat(), "late": current.late}
        # Decisions made outside Quantarium: pinned, and judged as possibly made with hindsight.
        decision_files = {}
        for key in [sleeve["decisions"] for sleeve in record["spec"]["sleeves"] if "decisions" in sleeve]:
            loaded = load_decisions(self.workspace.resolve(key))
            decision_files[key] = {"sha256": loaded.sha256, "first_date": loaded.dates[0].isoformat(),
                                   "last_date": loaded.dates[-1].isoformat()}
        model_card_sha256 = None
        if entry.get("model_card"):
            from .ml.card import load_card

            model_card_sha256 = load_card(self.workspace.resolve(entry["model_card"]))["sha256"]
        keys = params.get("scenarios")
        if not isinstance(keys, list) or not 1 <= len(keys) <= MAX_SCENARIOS or \
                not all(isinstance(key, str) for key in keys) or len(set(keys)) != len(keys):
            raise ValueError(f"scenarios must be 1-{MAX_SCENARIOS} distinct workspace keys")
        requested_maps = params.get("symbol_maps") or {}
        if not isinstance(requested_maps, dict) or set(requested_maps) - set(keys):
            raise ValueError("symbol_maps must name only selected scenarios")
        needed = set(entry["symbols"])
        pinned_scenarios = []
        for key in keys:
            directory = self.workspace.resolve(key)
            scenario = load_scenario(directory)
            if self.holdout is not None and (self.holdout.covers(directory)
                                             or scenario.manifest_sha256 in self.holdout.manifests()):
                raise ValueError("Holdout scenarios use the separate one-time holdout test")
            raw_map = requested_maps.get(key) or {}
            if not isinstance(raw_map, dict):
                raise ValueError(f"symbol_map for {key} must be an object")
            mapping = {str(source).upper(): str(target).upper()
                       for source, target in raw_map.items()}
            if not mapping and not needed <= set(scenario.symbols) and len(scenario.symbols) == len(needed):
                mapping = dict(zip(sorted(scenario.symbols), sorted(needed)))
            if not set(mapping) <= set(scenario.symbols):
                raise ValueError(f"symbol_map for {key} names symbols outside its scenario")
            provided = [mapping.get(symbol, symbol) for symbol in scenario.symbols]
            if len(set(provided)) != len(provided) or not needed <= set(provided):
                raise ValueError(f"Scenario {key} cannot supply every candidate symbol uniquely")
            pinned_scenarios.append({
                "key": self.workspace.key(directory), "name": scenario.name,
                "kind": scenario.kind, "manifest_sha256": scenario.manifest_sha256,
                "prices_sha256": _hash(scenario.prices_path),
                "actions_sha256": _hash(scenario.actions_path) if scenario.actions_path else None,
                "family": self._family(scenario), "lineage": self._lineage(scenario), "symbol_map": mapping,
                "start_date": scenario.start_date.isoformat(), "end_date": scenario.end_date.isoformat(),
                # Pinned only for imported data, so other registrations keep their fingerprints.
                **({"survivorship": scenario.survivorship} if scenario.survivorship else {}),
            })
        if len({item["manifest_sha256"] for item in pinned_scenarios}) != len(pinned_scenarios):
            raise ValueError("The same scenario content cannot appear twice in one experiment")
        if len({item["name"] for item in pinned_scenarios}) != len(pinned_scenarios):
            raise ValueError("Scenario names must be distinct within one experiment")
        rule = params.get("pass_rule") or {}
        if not isinstance(rule, dict) or set(rule) - {"min_mean_excess_return_pp", "max_drawdown_pct", "min_sessions"}:
            raise ValueError("pass_rule accepts min_mean_excess_return_pp, max_drawdown_pct, and min_sessions")
        min_sessions = rule.get("min_sessions", 60)
        if isinstance(min_sessions, bool) or not isinstance(min_sessions, int) or min_sessions < 20:
            raise ValueError("pass_rule.min_sessions must be a whole number of at least 20")
        max_drawdown = rule.get("max_drawdown_pct")
        pass_rule = {
            "min_mean_excess_return_pp": _nonnegative(rule.get("min_mean_excess_return_pp", 0),
                                                        "pass_rule.min_mean_excess_return_pp"),
            "max_drawdown_pct": None if max_drawdown is None else _nonnegative(
                max_drawdown, "pass_rule.max_drawdown_pct", maximum=100),
            "min_sessions": min_sessions,
        }
        slippage = _nonnegative(params.get("slippage_bps", 0), "slippage_bps")
        if slippage >= 10_000:
            raise ValueError("slippage_bps must be below 10000")
        # Only set costs are pinned, so registrations made before they existed keep their fingerprints.
        extra_costs = {name: value for name, value in cost_model(params).as_dict().items() if value}
        disguised_replay = params.get("disguised_replay", False)
        if not isinstance(disguised_replay, bool):
            raise ValueError("disguised_replay must be true or false")
        if inboxes and any(item["lineage"] != "historical" for item in pinned_scenarios):
            raise ValueError("Forward decisions were made in the real market; test them on scenarios of real "
                             "prices covering their dates, not on synthetic or transformed markets")
        if disguised_replay and (decision_files or inboxes):
            raise ValueError("A decision file's dates are fixed, so it cannot be replayed on a disguised market")
        return {
            "idea": idea, "name": name.strip(), "candidate_id": record["id"],
            "candidate_version": record["version"], "candidate": entry,
            "strategy_root": self.workspace.key(root), "script_sha256": script_hashes,
            "model_card_sha256": model_card_sha256, "ask_model": self._model(entry),
            "disguised_replay": disguised_replay,
            **({"decision_files": decision_files, "decisions": "after_the_fact"} if decision_files else {}),
            **({"inboxes": inboxes, "decisions": "forward"} if inboxes else {}),
            **({"decider": decider, "decisions": "live"} if decider else {}),
            "scenarios": pinned_scenarios, "pass_rule": pass_rule,
            "slippage_bps": slippage,
            "commission_per_order": _nonnegative(params.get("commission_per_order", 0),
                                                     "commission_per_order"),
            **({"costs": extra_costs} if extra_costs else {}),
        }

    def validate(self, params: dict) -> dict:
        """Validate a proposed registration without writing it."""
        snapshot = self._snapshot(params)
        return {"fingerprint": hashlib.sha256(_canonical(snapshot).encode()).hexdigest(),
                "candidate": snapshot["candidate"]["name"],
                "scenarios": [item["name"] for item in snapshot["scenarios"]]}

    def register(self, params: dict) -> dict:
        snapshot = self._snapshot(params)
        with self._lock:
            items = self._read()
            attempt = 1 + sum(item.get("spec", {}).get("idea") == snapshot["idea"] for item in items)
            record = {
                "id": "l_" + secrets.token_hex(6), "status": "registered",
                "created_utc": _now(), "started_utc": None, "finished_utc": None,
                "attempt_number": attempt,
                "fingerprint": hashlib.sha256(_canonical(snapshot).encode()).hexdigest(),
                "spec": snapshot, "consumed": False, "reused_families": [],
                "result": None, "error": None,
            }
            self._write(items + [record])
        return record

    def _verify(self, spec: dict) -> None:
        record = self.library.get(spec["candidate_id"])
        if record["version"] != spec["candidate_version"] or \
                self.library.engine_cell(record)[0] != _unfrozen(spec["candidate"]):
            raise ValueError("The candidate cell changed after registration")
        for key, digest in spec["script_sha256"].items():
            if _hash(self.workspace.resolve(key)) != digest:
                raise ValueError(f"Script {key} changed after registration")
        for strategy in current_value(spec["candidate"])["strategies"]:
            parameters = strategy.get("parameters") or {}
            if strategy["entrypoint"] == INBOX_ENTRYPOINT and \
                    pin_prefix(Path(parameters["inbox"]), parameters["until_bytes"]) != parameters["sha256"]:
                raise ValueError(f"The inbox {Path(parameters['inbox']).stem} changed before its registered end")
        for key, pin in (spec.get("decision_files") or {}).items():
            if _hash(self.workspace.resolve(key)) != pin["sha256"]:
                raise ValueError(f"Decision file {key} changed after registration")
        if spec.get("model_card_sha256") and \
                _hash(self.workspace.resolve(spec["candidate"]["model_card"])) != spec["model_card_sha256"]:
            raise ValueError("The model card changed after registration")
        if spec.get("ask_model") and self._model(spec["candidate"]) != spec["ask_model"]:
            raise ValueError("The asking model or its recorded training cutoff changed after registration")
        for pinned in spec["scenarios"]:
            scenario = load_scenario(self.workspace.resolve(pinned["key"]))
            if scenario.manifest_sha256 != pinned["manifest_sha256"] or \
                    _hash(scenario.prices_path) != pinned["prices_sha256"] or \
                    (_hash(scenario.actions_path) if scenario.actions_path else None) != pinned["actions_sha256"]:
                raise ValueError(f"Scenario {pinned['key']} changed after registration")

    def _suite(self, experiment_id: str, spec: dict) -> SuiteSpec:
        candidate = copy.deepcopy(spec["candidate"])
        names = _cell_names(candidate)
        controls = []
        for strategy, name in names["controls"].items():
            control = {"name": name, "mode": "complex", "symbols": candidate["symbols"],
                       "initial_cash": candidate["initial_cash"],
                       "strategies": [{"entrypoint": f"quantarium.lab_controls:{strategy}",
                                       "allocation": 1, "parameters": {}}]}
            if candidate.get("initial_positions"):
                control["initial_positions"] = candidate["initial_positions"]
            if candidate.get("index"):
                # Controls hold the same members as of each date, so the comparison is fair.
                control["index"] = candidate["index"]
            controls.append(control)
        # A hybrid also runs with each part switched off, to show what that part adds.
        variants = [{**copy.deepcopy(candidate), "name": name, "ablation": [part]}
                    for part, name in names["ablations"].items()]
        cells = _cells({"cells": [candidate, *controls, *variants]})
        scenarios = tuple(SuiteScenario(load_scenario(self.workspace.resolve(pin["key"])), pin["symbol_map"])
                          for pin in spec["scenarios"])
        database = self.workspace.resolve(DATABASE)
        database.parent.mkdir(parents=True, exist_ok=True)
        return SuiteSpec(experiment_id, str(database), spec["slippage_bps"],
                         spec["commission_per_order"], cells, scenarios, None,
                         costs=CostModel(**spec.get("costs", {})))

    def _statistics(self, record: dict, runs: list[dict]) -> dict:
        """Trial-aware statistics for the candidate: this attempt and the earlier ones at the same idea."""
        database = self.workspace.resolve(DATABASE)

        def series(item: dict, item_runs: list[dict]) -> dict[str, list[tuple[str, float]]]:
            """The candidate's daily returns in each scenario of an attempt, keyed by the scenario's prices."""
            pins = {pin["name"]: pin for pin in item["spec"]["scenarios"]}
            found = {}
            for entry in item_runs:
                pin = pins.get(entry.get("scenario"))
                if pin and pin.get("prices_sha256"):
                    found[pin["prices_sha256"]] = daily_returns(database, entry["run_id"],
                                                                item["spec"]["candidate"]["name"])
            return found

        spec = record["spec"]
        current = series(record, runs)
        earlier = [series(item, item["result"].get("runs") or []) for item in self._read()
                   if item["id"] != record["id"] and item.get("spec", {}).get("idea") == spec["idea"]
                   and item.get("status") == "completed" and isinstance(item.get("result"), dict)]
        # Every registration of the idea counts as a try, including ones that failed or went stale.
        trials = max(record["attempt_number"], 1 + len(earlier))
        rows, pbo = [], None
        for pin in spec["scenarios"]:
            returns = current.get(pin.get("prices_sha256")) or []
            peers = [other[pin["prices_sha256"]] for other in earlier if other.get(pin.get("prices_sha256"))]
            sharpes = [stats["sharpe"] for stats in (moments([value for _, value in peer]) for peer in [returns, *peers])
                       if stats]
            computed = deflated_sharpe([value for _, value in returns], trials, sharpes)
            rows.append({"scenario": pin["name"],
                         **(computed or {"psr": None, "dsr": None, "trials": trials,
                                         "reason": "too few sessions, or returns that never varied"})})
            if pbo is None and 1 + len(peers) >= PBO_MIN_TRIALS:
                pbo = {"scenario": pin["name"], **overfitting_probability(
                    {"this attempt": returns, **{f"attempt {index + 1}": peer for index, peer in enumerate(peers)}})}
        return {"rows": rows, "attempts": trials, "attempts_with_results": 1 + len(earlier),
                "pbo": pbo or {"pbo": None, "attempts": 1 + len(earlier),
                               "reason": f"needs at least {PBO_MIN_TRIALS} completed attempts at this idea on shared data"},
                "dsr_threshold": DSR_THRESHOLD}

    def _verdict(self, spec: dict, report: dict, reused: list[str], statistics: dict | None = None) -> dict:
        candidate = spec["candidate"]["name"]
        names = _cell_names(spec["candidate"])
        grant = spec["candidate"].get("ask")
        # A candidate with a grant asks a model, even in a record without a pinned cutoff.
        asking = spec.get("ask_model") or spec.get("decider") or \
            ({"model": grant.get("model"), "training_cutoff": None} if grant else None)
        cutoff = asking.get("training_cutoff") if asking else None
        rows = []
        for scenario in spec["scenarios"]:
            matching = {row["cell"]: row for row in report["matrix"] if row["scenario"] == scenario["name"]}
            target = matching.get(candidate)
            controls = [matching[name] for name in names["controls"].values() if name in matching]
            if target is None or len(controls) != 2:
                raise ValueError("The Lab report is missing the candidate or controls")
            returns = [row["total_return_pct"] for row in controls]
            excess = (target["total_return_pct"] - max(returns)
                      if target["total_return_pct"] is not None and all(value is not None for value in returns)
                      else None)
            rows.append({"scenario": scenario["name"], "kind": scenario["kind"],
                         "start_date": scenario["start_date"], "end_date": scenario["end_date"],
                         "family": scenario["family"],
                         "candidate_return_pct": target["total_return_pct"],
                         "best_control_return_pct": max(returns) if all(value is not None for value in returns) else None,
                         "controls": [{"name": control["cell"],
                                       "return_pct": control["total_return_pct"]}
                                      for control in controls],
                         "excess_return_pp": excess, "drawdown_pct": target["max_drawdown_pct"],
                         "sessions": target["sessions"], "status": target["status"],
                         "error": target.get("error"),
                         "lineage": _pinned_lineage(scenario),
                         "survivorship": _survivorship(scenario),
                         "model_contamination": None if not asking else _contamination(scenario, cutoff),
                         "ablations": [{"part": part, "cell": name,
                                        "return_pct": matching.get(name, {}).get("total_return_pct"),
                                        "contribution_pp": (target["total_return_pct"]
                                                            - matching[name]["total_return_pct"])
                                        if target["total_return_pct"] is not None and name in matching
                                        and matching[name]["total_return_pct"] is not None else None}
                                       for part, name in names["ablations"].items()]})
        caveats = list(report["caveats"])
        for row in rows:
            if row["status"] != "completed":
                caveats.append(f"The candidate {row['status']} on {row['scenario']}"
                               + (f": {row['error']}" if row.get("error") else "."))
        caveats += ["Attempts observed in this workspace exclude offline or renamed trials.",
                    "Quantarium pins entrypoint files, but cannot verify transitive imports or undisclosed data access."]
        if len(rows) == 1:
            caveats.append("Only one scenario was tested; the result is sensitive to that scenario's design.")
        pins = spec["scenarios"]
        if len({pin["family"] for pin in pins}) < len(pins) or len({pin.get("prices_sha256") for pin in pins}) < len(pins):
            caveats.append("Some scenarios share source data; their outcomes are not independent evidence.")
        if reused:
            caveats.append("This idea already used related scenario data; this run is exploratory only.")
        caveats += _data_caveats(spec)
        after_the_fact = spec.get("decisions") == "after_the_fact"
        if after_the_fact:
            caveats.append("The candidate replays a finished decision file. Quantarium cannot tell whether those decisions "
                           "were made at the time or afterwards, with hindsight, so this result is exploratory: it shows "
                           "costs, controls, and drawdown, but cannot support or reject the idea.")
            for key, pin in spec["decision_files"].items():
                idle = [row["scenario"] for row in rows
                        if pin["last_date"] < row["start_date"] or pin["first_date"] > row["end_date"]]
                if idle:
                    caveats.append(f"{key} has no decisions during {', '.join(idle)}, so the candidate only held there.")
        if spec.get("decider"):
            caveats.append(f"The candidate is {spec['decider']['model']}, an outside program. Quantarium showed it only data "
                           "up to each session, but cannot stop it from reading other sources, so on real history it "
                           "is judged like a model that may remember what happened.")
        for name, frozen in (spec.get("inboxes") or {}).items():
            caveats.append(f"The candidate's decisions came from the inbox {name}, frozen at registration. Each counted "
                           "only if this computer's clock showed it arriving before the next session opened.")
            if frozen["late"]:
                caveats.append(f"{frozen['late']} late decision{'s' if frozen['late'] != 1 else ''} in {name} "
                               "were left out.")
            early = [row["scenario"] for row in rows if row["start_date"] < frozen["first_on_time"]]
            if early:
                caveats.append(f"{', '.join(early)} began before {name}'s first on-time decision "
                               f"({frozen['first_on_time']}); the candidate held cash until then.")
        boundary = spec["candidate"].get("information_end")
        bound_model = bool(spec.get("model_card_sha256"))
        if boundary and not bound_model:
            caveats.append("The information_end date is self-declared and unverified.")
        if bound_model:
            caveats.append("The model card pins training bytes and label timing, but its source claim and any unseen training are not independently verified.")
        contaminated = bool(boundary and bound_model and
                            any(row["start_date"] <= boundary for row in rows))
        remembered = bool(asking) and any(row["model_contamination"] != "clean" for row in rows)
        if asking and cutoff is None:
            caveats.append("The candidate asks a language model with no recorded training cutoff. Only synthetic "
                           "scenarios can judge it; on real history the model may remember what happened.")
        elif asking:
            caveats.append(f"The candidate asks {asking['model']}, recorded as trained through {cutoff}. That date is "
                           "the provider's disclosure as entered by the user; Quantarium cannot verify it.")
            stale = [row["scenario"] for row in rows if row["model_contamination"] == "contaminated"]
            if stale:
                caveats.append(f"{', '.join(stale)} began on or before the model's training cutoff, so the model "
                               "may remember what happened.")
        for part, label in (("ask", "the language model"), ("risk", "the risk card"),
                            ("allocator", "its allocator (with fixed allocations instead)")):
            contributions = [entry["contribution_pp"] for row in rows for entry in row["ablations"]
                             if entry["part"] == part and entry["contribution_pp"] is not None]
            if contributions:
                mean = sum(contributions) / len(contributions)
                caveats.append(f"Without {label}, the candidate's return changed by {-mean:+.2f} percentage points on "
                               "average" + ("; that part added nothing measurable here." if mean <= MATCH_TOLERANCE_PP
                                            else "."))
        if contaminated:
            caveats.append("At least one scenario begins on or before the model's final training label; its whole-run return cannot support this verdict.")
        if reused or boundary and not bound_model or contaminated or remembered or after_the_fact:
            status = "unknown"
        elif any(row["status"] != "completed" or row["excess_return_pp"] is None
                 or row["drawdown_pct"] is None for row in rows):
            status = "unknown"
        elif any(row["sessions"] < spec["pass_rule"]["min_sessions"] for row in rows):
            status = "inconclusive"
            caveats.append("At least one scenario has fewer sessions than the registered minimum.")
        elif all(abs(row["excess_return_pp"]) <= MATCH_TOLERANCE_PP for row in rows):
            # Doing exactly as well as a simple control is not evidence for the idea.
            status = "not_supported"
            caveats.append("The candidate matched its best control in every scenario; it added nothing over it.")
        else:
            mean = sum(row["excess_return_pp"] for row in rows) / len(rows)
            drawdown = max(row["drawdown_pct"] for row in rows)
            rule = spec["pass_rule"]
            # The edge must exceed the registered threshold, so a tie never passes.
            passed = mean > rule["min_mean_excess_return_pp"] and \
                (rule["max_drawdown_pct"] is None or drawdown <= rule["max_drawdown_pct"])
            status = "supported_within_tested_scope" if passed else "not_supported"
        if statistics:
            weak = [row["scenario"] for row in statistics["rows"] if row.get("dsr") is None or row["dsr"] < DSR_THRESHOLD]
            if status == "supported_within_tested_scope" and weak:
                status = "inconclusive"
                caveats.append(f"The candidate beat its controls, but after allowing for {statistics['attempts']} "
                               f"attempt{'s' if statistics['attempts'] != 1 else ''} at this idea and its returns' "
                               f"shape, its deflated Sharpe ratio is below {DSR_THRESHOLD} in {', '.join(weak)}: "
                               "the edge could be luck. More sessions, or fewer tries, would settle it.")
            pbo = (statistics.get("pbo") or {}).get("pbo")
            if pbo is not None and pbo >= 0.5:
                caveats.append(f"Across the attempts at this idea, the one that looked best on half the data ranked "
                               f"below the median on the other half {pbo:.0%} of the time: a sign of overfitting.")
        return {"status": status, "rows": rows, "pass_rule": spec["pass_rule"], "statistics": statistics,
                "caveats": caveats, "used_data": bool(reused),
                "observed_attempt_number": None,
                "scope": {"universe": spec["candidate"]["symbols"],
                          **({"index": spec["candidate"]["index"]} if spec["candidate"].get("index") else {}),
                          "slippage_bps": spec["slippage_bps"],
                          "commission_per_order": spec["commission_per_order"],
                          **({"costs": spec["costs"]} if spec.get("costs") else {}),
                          "information_end": boundary,
                          "information_boundary_status": "model_card_bound" if bound_model else
                          "self_declared_unverified" if boundary else "not_supplied",
                          "model_card_sha256": spec.get("model_card_sha256"),
                          "model": asking["model"] if asking else None,
                          "model_training_cutoff": cutoff,
                          "model_contamination": None if not asking else {
                              label: sum(row["model_contamination"] == label for row in rows)
                              for label in ("clean", "contaminated", "unknown")}}}

    def _disguise(self, experiment_id: str, fingerprint: str, spec: dict, verdict: dict) -> dict:
        """Rerun the candidate on disguised copies of its real-history scenarios; a diagnostic only."""
        candidate = spec["candidate"]
        rows, caveats = [], []
        originals = {row["scenario"]: row for row in verdict["rows"]}
        for index, pin in enumerate(spec["scenarios"]):
            if pin.get("lineage") == "synthetic":
                continue
            row = {"scenario": pin["name"], "original_return_pct": originals[pin["name"]]["candidate_return_pct"],
                   "disguised_return_pct": None, "gap_pp": None, "status": None, "error": None}
            rows.append(row)
            try:
                disguised, invented = disguise_scenario(
                    self.workspace.resolve(pin["key"]),
                    self.workspace.root / DISGUISE_FOLDER / experiment_id / f"s{index}",
                    seed=f"{fingerprint}:{index}", name=f"disguised_{index}")
            except ValueError as exc:
                row.update(status="skipped", error=str(exc))
                continue
            # The cell's symbols, through the scenario's map, to their invented names.
            names = {pin["symbol_map"].get(symbol, symbol): fake for symbol, fake in invented.items()}
            entry = copy.deepcopy(candidate)
            entry.update(name=f"{candidate['name']}__disguised", symbols=[names[s] for s in candidate["symbols"]])
            entry["strategies"] = [{**strategy, "parameters": _renamed(strategy.get("parameters", {}), names)}
                                   for strategy in candidate["strategies"]]
            if entry.get("initial_positions"):
                entry["initial_positions"] = {names[symbol]: value
                                              for symbol, value in entry["initial_positions"].items()}
            suite = SuiteSpec(f"{experiment_id}-disguised-{index}", str(self.workspace.resolve(DATABASE)),
                              spec["slippage_bps"], spec["commission_per_order"], _cells({"cells": [entry]}),
                              (SuiteScenario(disguised, {}),), None, costs=CostModel(**spec.get("costs", {})))
            self._update(experiment_id, progress={"stage": "The disguised replay", "scenario": pin["name"],
                                                  "step": index + 1, "of": len(spec["scenarios"]),
                                                  "updated_utc": _now()})
            run_suite(suite, _canonical({"lab": experiment_id, "disguise": index}),
                      self.workspace.resolve(spec["strategy_root"]))
            result = next(item for item in suite_report(suite)["matrix"] if item["cell"] == entry["name"])
            row.update(disguised_return_pct=result["total_return_pct"], status=result["status"],
                       error=result.get("error"))
            if row["original_return_pct"] is not None and row["disguised_return_pct"] is not None:
                row["gap_pp"] = row["original_return_pct"] - row["disguised_return_pct"]
        gaps = [row["gap_pp"] for row in rows if row["gap_pp"] is not None]
        if gaps:
            mean = sum(gaps) / len(gaps)
            caveats.append(f"On disguised copies (invented symbols, future dates, rescaled prices), the candidate "
                           f"returned {abs(mean):.2f} percentage points {'less' if mean >= 0 else 'more'} on average. A large gap "
                           "suggests it depends on recognising the market; the copies also change names, dates, "
                           "and price levels, which a strategy may legitimately use.")
        if any(row["status"] not in {"completed", "skipped"} for row in rows):
            caveats.append("The candidate did not complete on a disguised copy; it may depend on the real symbols "
                           "or dates.")
        if not rows:
            caveats.append("Every scenario is synthetic, so there was nothing to disguise.")
        return {"rows": rows, "caveats": caveats}

    def run(self, experiment_id: str) -> dict:
        record = self.get(experiment_id)
        if record["status"] != "registered":
            raise QuantariumError("A Lab experiment can run only once", "lab_already_run")
        spec = record["spec"]
        try:
            self._verify(spec)
        except (OSError, ValueError, QuantariumError) as exc:
            self._update(experiment_id, status="stale", finished_utc=_now(), error=str(exc))
            raise
        with self._lock:
            items = self._read()
            families = {name for pin in spec["scenarios"] for name in _data_names(pin)}
            reused = sorted(families & {name for item in items
                                        if item.get("id") != experiment_id and item.get("consumed")
                                        and item.get("spec", {}).get("idea") == spec["idea"]
                                        for pin in item["spec"]["scenarios"] for name in _data_names(pin)})
            for item in items:
                if item["id"] == experiment_id:
                    if item["status"] != "registered":
                        raise QuantariumError("A Lab experiment can run only once", "lab_already_run")
                    item.update(status="running", started_utc=_now(), consumed=True,
                                reused_families=reused)
                    break
            self._write(items)
        def progress(stage: str):
            def report(index: int, total: int, scenario: str) -> None:
                self._update(experiment_id, progress={"stage": stage, "scenario": scenario,
                                                      "step": index + 1, "of": total, "updated_utc": _now()})
            return report

        try:
            suite = self._suite(experiment_id, spec)
            run = run_suite(suite, _canonical({"lab": experiment_id, "fingerprint": record["fingerprint"]}),
                            self.workspace.resolve(spec["strategy_root"]),
                            on_scenario=progress("The candidate, its controls, and its variants"))
            self._verify(spec)
            report = suite_report(suite)
            statistics = self._statistics(record, run["runs"])
            verdict = self._verdict(spec, report, reused, statistics)
            verdict["observed_attempt_number"] = record["attempt_number"]
            verdict["disguise"] = self._disguise(experiment_id, record["fingerprint"], spec, verdict) \
                if spec.get("disguised_replay") else None
            result = {"runs": run["runs"], "report": report, "verdict": verdict,
                      "database": DATABASE}
        except Exception as exc:
            self._update(experiment_id, status="failed", finished_utc=_now(),
                         error=f"{type(exc).__name__}: {exc}")
            raise
        return self._update(experiment_id, status="completed", finished_utc=_now(), result=result, progress=None)


def _contamination(pin: dict, cutoff: str | None) -> str:
    """Whether a language model could remember a scenario: clean, contaminated, or unknown."""
    lineage = _pinned_lineage(pin)
    if lineage == "synthetic":
        return "clean"
    if lineage != "historical" or cutoff is None:
        return "unknown"
    return "clean" if pin["start_date"] > cutoff else "contaminated"


def _survivorship(pin: dict) -> str | None:
    """What the user declared about survivorship: None for synthetic markets, which have no survivors."""
    if _pinned_lineage(pin) == "synthetic":
        return None
    return pin.get("survivorship") or "not_declared"


def _data_caveats(spec: dict) -> list[str]:
    """Survivorship and selection: real-price results that the data or the symbol list may flatter."""
    caveats = []
    real = [pin for pin in spec["scenarios"] if _pinned_lineage(pin) != "synthetic"]
    unclean = [pin["name"] for pin in real if _survivorship(pin) != "survivorship_free"]
    if unclean:
        caveats.append(f"{', '.join(unclean)} {'are' if len(unclean) > 1 else 'is'} real-price data not declared free "
                       f"of survivorship bias: companies that failed or were delisted may be missing, which usually "
                       f"flatters a strategy. Survivorship-free data in Quantarium's format (docs/data-format.md) removes "
                       f"this caveat.")
    if real and not spec["candidate"].get("index") and spec.get("decisions") != "after_the_fact":
        count = len(spec["candidate"]["symbols"])
        caveats.append(f"The candidate trades a fixed list of {count} symbol{'s' if count != 1 else ''} on real-price "
                       f"data. If that list was chosen knowing how those stocks later did, the result is flattered; a "
                       f"cell that holds an index's members as of each date (index) avoids this.")
    return caveats


def _pinned_lineage(pin: dict) -> str:
    # Records from before v0.4.5 pinned no lineage; a transformed scenario's origin is then unknown.
    return pin.get("lineage") or (pin["kind"] if pin.get("kind") in {"synthetic", "historical"} else "unknown")


def _unfrozen(candidate: dict) -> dict:
    """A registered candidate without its inbox freeze or decider pin, to compare with the cell as it is now."""
    candidate = current_value(copy.deepcopy(candidate))
    for strategy in candidate.get("strategies", []):
        if strategy.get("entrypoint") == INBOX_ENTRYPOINT:
            for key in ("until_bytes", "sha256"):
                strategy.get("parameters", {}).pop(key, None)
        if strategy.get("entrypoint") == DECIDER_ENTRYPOINT:
            strategy.get("parameters", {}).pop("fingerprint", None)
    return candidate
