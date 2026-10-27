"""Turn library cells into Static runs, scenario suites, and Feed experiments.

Every deployment records the cell's id and version, both in the cell's file
and in the run's metadata, so a cell's results can be found again. Suite and
Feed deployments write a config under ``configs/generated/``; it can be rerun
from the command line like any other config.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path

from .analysis import read_analysis
from .cells import CellLibrary
from .config import load_config, load_feed_config, strategy_root
from .engine import run_static
from .errors import QuantariumError
from .feed import provider_for, run_feed_batch
from .scenario import load_scenario
from .suite import load_suite, run_suite, suite_report

GENERATED = "configs/generated"
_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    return _SLUG.sub("-", text.lower()).strip("-")[:40] or "cells"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _relative(path: Path, start: Path) -> str:
    return Path(os.path.relpath(path, start)).as_posix()


class Deployer:
    def __init__(self, workspace, library: CellLibrary) -> None:
        self.workspace = workspace
        self.library = library
        self.static_database = "runs/cells.sqlite"
        self.suite_database = "runs/suites.sqlite"

    def _cells(self, cell_ids: list[str]) -> tuple[list[dict], list[dict], Path]:
        if not cell_ids:
            raise ValueError("Choose at least one cell")
        records, entries, roots = [], [], set()
        for cell_id in dict.fromkeys(cell_ids):
            record = self.library.get(cell_id)
            entry, root = self.library.engine_cell(record)
            records.append(record)
            entries.append(entry)
            roots.add(root)
        if len(roots) > 1:
            raise ValueError("Cells deployed together must use scripts from one folder")
        return records, entries, roots.pop()

    def _write_config(self, kind: str, label: str, config: dict) -> Path:
        folder = self.workspace.resolve(GENERATED)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{kind}-{_slug(label)}.json"
        path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        return path

    @staticmethod
    def _metadata(records: list[dict]) -> dict:
        return {"origin": "cell-library",
                "cells": {record["name"]: {"id": record["id"], "version": record["version"]}
                          for record in records}}

    def static(self, cell_ids: list[str], data: str, *, actions: str | None = None,
               slippage_bps: float = 0, commission_per_order: float = 0,
               database: str | None = None, extra_costs: dict | None = None) -> dict:
        records, entries, root = self._cells(cell_ids)
        folder = self.workspace.resolve(GENERATED)
        data_path = self.workspace.resolve(data)
        database_path = self.workspace.resolve(database or self.static_database)
        config = {"data_path": _relative(data_path, folder),
                  "database_path": _relative(database_path, folder),
                  "slippage_bps": slippage_bps, "commission_per_order": commission_per_order, **(extra_costs or {}),
                  "strategy_root": _relative(root, folder), "cells": entries}
        if actions:
            config["actions_path"] = _relative(self.workspace.resolve(actions), folder)
        label = f"{_stamp()}-{records[0]['name']}"
        path = self._write_config("static", label, config)
        raw = path.read_text(encoding="utf-8")
        spec, _ = load_config(path)
        run_id = run_static(spec, raw, strategy_root(path, raw), self._metadata(records))
        db_key = self.workspace.key(database_path)
        for record in records:
            self.library.record_deployment(record["id"], {
                "kind": "static", "db": db_key, "id": run_id,
                "label": Path(data).name, "config": self.workspace.key(path),
            })
        return {"kind": "static", "db": db_key, "id": run_id, "config": self.workspace.key(path)}

    def suite(self, cell_ids: list[str], scenarios: list[str], *, name: str | None = None,
              benchmark: str | None = None, slippage_bps: float = 0,
              commission_per_order: float = 0, symbol_maps: dict | None = None,
              extra_costs: dict | None = None) -> dict:
        records, entries, root = self._cells(cell_ids)
        if not scenarios:
            raise ValueError("Choose at least one scenario")
        needed = sorted({symbol for entry in entries for symbol in entry["symbols"]})
        folder = self.workspace.resolve(GENERATED)
        listed, mappings = [], {}
        for key in scenarios:
            directory = self.workspace.resolve(key)
            scenario = load_scenario(directory)
            mapping = dict((symbol_maps or {}).get(key, {}))
            if not mapping and not set(needed) <= set(scenario.symbols):
                if len(scenario.symbols) != len(needed):
                    raise ValueError(
                        f"Scenario {scenario.name} has {len(scenario.symbols)} symbols but the "
                        f"cells use {len(needed)}; give a symbol map"
                    )
                # Same count: pair them in sorted order, and report it.
                mapping = dict(zip(sorted(scenario.symbols), needed))
            mappings[scenario.name] = mapping
            listed.append({"path": _relative(directory, folder), "symbol_map": mapping}
                          if mapping else _relative(directory, folder))
        benchmark_name = None
        if benchmark:
            benchmark_name = next((record["name"] for record in records
                                   if record["id"] == benchmark), None)
            if benchmark_name is None:
                raise ValueError("The benchmark must be one of the deployed cells")
        label = name or f"{_stamp()}-{records[0]['name']}"
        suite_name = _slug(label).replace("-", "_")[:64]
        config = {"name": suite_name,
                  "database_path": _relative(self.workspace.resolve(self.suite_database), folder),
                  "slippage_bps": slippage_bps, "commission_per_order": commission_per_order, **(extra_costs or {}),
                  "strategy_root": _relative(root, folder), "scenarios": listed, "cells": entries}
        if benchmark_name:
            config["benchmark"] = benchmark_name
        path = self._write_config("suite", label, config)
        raw = path.read_text(encoding="utf-8")
        spec, _ = load_suite(path)
        result = run_suite(spec, raw, strategy_root(path, raw))
        config_key = self.workspace.key(path)
        for record in records:
            self.library.record_deployment(record["id"], {
                "kind": "suite", "config": config_key, "db": self.workspace.key(Path(spec.database_path)),
                "id": suite_name, "label": f"{len(scenarios)} scenarios",
            })
        return {"kind": "suite", "config": config_key, "runs": result["runs"],
                "symbol_maps": mappings}

    def feed(self, cell_ids: list[str], *, feed_id: str, provider: str | None = None,
             source: str | None = None, start_date: str | None = None,
             slippage_bps: float = 0, commission_per_order: float = 0,
             through: str | None = None, extra_costs: dict | None = None) -> dict:
        """Create a generated Feed experiment, or add cells to one, then advance it."""
        records, entries, root = self._cells(cell_ids)
        folder = self.workspace.resolve(GENERATED)
        path = folder / f"feed-{_slug(feed_id)}.json"
        if path.is_file():
            config = json.loads(path.read_text(encoding="utf-8"))
            existing = {cell["name"] for cell in config["cells"]}
            clashes = sorted(existing & {entry["name"] for entry in entries})
            if clashes:
                raise QuantariumError(f"Already in {feed_id}: {', '.join(clashes)}", "already_in_experiment",
                                   "Use a new feed_id, or deploy cells that are not in it yet")
            if config.get("strategy_root") != _relative(root, folder):
                raise ValueError("New cells must use the same script folder as the experiment")
            config["cells"] += entries
        else:
            if provider not in {"csv", "scenario"}:
                raise ValueError("provider must be csv or scenario")
            if not source or not start_date:
                raise ValueError("A new Feed needs a source and a start date")
            date.fromisoformat(start_date)
            config = {"feed_id": feed_id, "provider": provider, "start_date": start_date,
                      ("data_path" if provider == "csv" else "scenario_path"):
                          _relative(self.workspace.resolve(source), folder),
                      "database_path": _relative(
                          self.workspace.resolve(f"runs/feed-{_slug(feed_id)}.sqlite"), folder),
                      "slippage_bps": slippage_bps, "commission_per_order": commission_per_order, **(extra_costs or {}),
                      "strategy_root": _relative(root, folder), "cells": entries}
        folder.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        raw = path.read_text(encoding="utf-8")
        spec, _ = load_feed_config(path)
        report = run_feed_batch(spec, raw, strategy_root(path, raw), provider_for(spec),
                                through=date.fromisoformat(through) if through else None)
        db_key = self.workspace.key(Path(spec.database_path))
        for record in records:
            self.library.record_deployment(record["id"], {
                "kind": "feed", "db": db_key, "id": spec.feed_id,
                "config": self.workspace.key(path), "label": spec.provider,
            })
        return {"kind": "feed", "config": self.workspace.key(path), "db": db_key,
                "id": spec.feed_id, "days_processed": report.get("days_processed_this_call")}


def name_for_version(record: dict, version: int) -> str:
    """The name a cell had when that version was deployed."""
    return next((entry["name"] for entry in [*record.get("history", []), record]
                 if entry.get("version") == version), record["name"])


def arena(workspace, library: CellLibrary) -> list[dict]:
    """Every Feed Batch experiment and its cells, linked to library cells where possible."""
    owners: dict[tuple[str, str, str], dict] = {}
    for record in library.all():
        for deployment in record["deployments"]:
            if deployment["kind"] == "feed":
                key = (deployment["db"], deployment["id"],
                       name_for_version(record, deployment["version"]))
                owners[key] = record
    configs = {config["name"]: config["key"] for config in workspace.configs()
               if config["kind"] == "feed"}
    result = []
    for database in workspace.databases():
        for experiment in database["experiments"]:
            if experiment["kind"] != "feed":
                continue
            entry = {**experiment, "db": database["key"], "config": configs.get(experiment["id"]),
                     "cells": []}
            try:
                analysis = read_analysis(workspace.resolve(database["key"]), "feed", experiment["id"])
            except (OSError, ValueError, KeyError) as exc:
                entry["error"] = f"{type(exc).__name__}: {exc}"
                result.append(entry)
                continue
            for cell in analysis["cells"]:
                record = owners.get((database["key"], experiment["id"], cell["name"]))
                curve = [row for row in analysis["curve"] if row["cell"] == cell["name"]]
                entry["cells"].append({
                    **{key: cell.get(key) for key in (
                        "name", "status", "error", "total_return_pct", "forward_return_pct",
                        "forward_sessions", "backfill_sessions", "sessions", "max_drawdown_pct",
                        "sharpe_ratio_rf0", "last_date", "input")},
                    "cell_id": record["id"] if record else None,
                    "mode": record["spec"]["mode"] if record else None,
                    "archived": record["archived"] if record else False,
                    "spark": sample([row["index_100"] for row in curve], 60),
                })
            result.append(entry)
    # Active experiments first, most recently advanced first within each group.
    result.sort(key=lambda item: item["last_date"] or "", reverse=True)
    return sorted(result, key=lambda item: item["status"] != "active")


def cell_result(workspace, record: dict, deployment: dict | None = None) -> dict | None:
    """Headline numbers of one deployment, for a card or the cell page."""
    deployments = record.get("deployments", [])
    if deployment is None:
        if not deployments:
            return None
        deployment = deployments[-1]
    name = name_for_version(record, deployment["version"])
    try:
        if deployment["kind"] == "suite":
            spec, _ = load_suite(workspace.resolve(deployment["config"]))
            rows = [row for row in suite_report(spec)["matrix"] if row["cell"] == name]
            if not rows:
                return None
            first = read_analysis(workspace.resolve(deployment["db"]), "static", rows[0]["run_id"])
            spark = sample([point["index_100"] for point in first["curve"] if point["cell"] == name], 60)
            returns = [row["total_return_pct"] for row in rows if row["total_return_pct"] is not None]
            sharpes = [row["sharpe_ratio_rf0"] for row in rows if row["sharpe_ratio_rf0"] is not None]
            return {"deployment": deployment, "evidence": "scenario suite", "cell_name": name,
                    "total_return_pct": sum(returns) / len(returns) if returns else None,
                    "max_drawdown_pct": max((row["max_drawdown_pct"] or 0) for row in rows),
                    "sharpe_ratio_rf0": sum(sharpes) / len(sharpes) if sharpes else None,
                    "sessions": sum(row["sessions"] for row in rows),
                    "scenarios": len(rows), "status": rows[0]["status"], "spark": spark,
                    "spark_label": rows[0]["scenario"]}
        analysis = read_analysis(workspace.resolve(deployment["db"]), deployment["kind"],
                                 deployment["id"])
    except (OSError, ValueError, KeyError):
        return {"deployment": deployment, "error": "Result is no longer readable"}
    summary = next((cell for cell in analysis["cells"] if cell["name"] == name), None)
    if summary is None:
        return None
    curve = [row for row in analysis["curve"] if row["cell"] == name]
    evidence = "historical replay"
    if deployment["kind"] == "feed":
        evidence = ("scenario" if summary.get("input") == "scenario" else
                    "forward test" if summary.get("forward_sessions") else "feed backfill")
    return {"deployment": deployment, "evidence": evidence, "cell_name": name,
            **{key: summary.get(key) for key in (
                "total_return_pct", "max_drawdown_pct", "sharpe_ratio_rf0",
                "annualized_volatility_pct", "sessions", "status", "forward_sessions",
                "forward_return_pct", "turnover_ratio", "average_exposure_pct")},
            "spark": sample([point["index_100"] for point in curve], 60)}


def sample(values: list[float], limit: int) -> list[float]:
    """At most ``limit`` evenly spaced values, always keeping the first and last."""
    if len(values) <= limit:
        return [round(value, 4) for value in values]
    picks = {round(index * (len(values) - 1) / (limit - 1)) for index in range(limit)}
    return [round(values[index], 4) for index in sorted(picks)]
