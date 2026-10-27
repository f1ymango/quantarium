"""Run one set of cells across several scenarios and compare the results."""

from __future__ import annotations

import csv
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from .analysis import read_analysis
from .config import _cells, _finite_nonnegative, cost_model, plain_errors
from .engine import run_static
from .model import CellSpec, CostModel, RunSpec
from .scenario import Scenario, _NAME, load_scenario

METRICS = (
    "total_return_pct", "max_drawdown_pct", "annualized_volatility_pct", "sharpe_ratio_rf0",
    "turnover_ratio", "average_exposure_pct", "excess_return_pp",
)
CAVEATS = {
    "synthetic": "Synthetic: shows sensitivity to a market shape someone specified, "
                 "not how the strategy behaved in a real market.",
    "transformed": "Transformed: real or synthetic bars altered on purpose; "
                   "the altered period did not happen.",
    "historical": "Historical: symbols chosen from today's lists carry survivorship bias.",
    "historical_declared_free": "Historical: imported data its supplier declared free of survivorship bias; "
                                "Quantarium records that declaration but cannot verify it.",
}


def _caveat_kind(scenario) -> str:
    if scenario.kind == "historical" and scenario.survivorship == "survivorship_free":
        return "historical_declared_free"
    return scenario.kind


@dataclass(frozen=True)
class SuiteScenario:
    scenario: Scenario
    symbol_map: dict[str, str]


@dataclass(frozen=True)
class SuiteSpec:
    name: str
    database_path: str
    slippage_bps: float
    commission_per_order: float
    cells: tuple[CellSpec, ...]
    scenarios: tuple[SuiteScenario, ...]
    benchmark: str | None
    costs: CostModel = field(default_factory=CostModel)


@plain_errors
def load_suite(path: Path) -> tuple[SuiteSpec, str]:
    raw = path.read_text(encoding="utf-8")
    source = json.loads(raw)
    name = str(source.get("name", ""))
    if not _NAME.fullmatch(name):
        raise ValueError("Suite name must use 1-64 letters, digits, dashes, or underscores")
    root = path.resolve().parent
    cells = _cells(source)
    needed = {symbol for cell in cells for symbol in cell.symbols}
    entries = source.get("scenarios")
    if not isinstance(entries, list) or not entries:
        raise ValueError("A suite needs a nonempty scenarios list")
    scenarios: list[SuiteScenario] = []
    seen: set[str] = set()
    for entry in entries:
        if isinstance(entry, str):
            entry = {"path": entry}
        scenario = load_scenario((root / entry["path"]).resolve())
        if scenario.name in seen:
            raise ValueError(f"Scenario {scenario.name} appears twice in the suite")
        seen.add(scenario.name)
        mapping = {str(key).upper(): str(value).upper()
                   for key, value in entry.get("symbol_map", {}).items()}
        if not set(mapping) <= set(scenario.symbols):
            raise ValueError(f"symbol_map for {scenario.name} names symbols it does not have")
        provided = [mapping.get(symbol, symbol) for symbol in scenario.symbols]
        if len(set(provided)) != len(provided):
            raise ValueError(f"symbol_map for {scenario.name} maps two symbols to one")
        missing = needed - set(provided)
        if missing:
            raise ValueError(
                f"Scenario {scenario.name} does not provide {', '.join(sorted(missing))}; "
                "add them to the scenario or map its symbols with symbol_map"
            )
        scenarios.append(SuiteScenario(scenario, mapping))
    benchmark = source.get("benchmark")
    if benchmark is not None and benchmark not in {cell.name for cell in cells}:
        raise ValueError(f"Benchmark cell not found: {benchmark}")
    spec = SuiteSpec(
        name=name, database_path=str((root / source["database_path"]).resolve()),
        slippage_bps=_finite_nonnegative(source.get("slippage_bps", 0), "slippage_bps"),
        commission_per_order=_finite_nonnegative(source.get("commission_per_order", 0),
                                                 "commission_per_order"),
        cells=cells, scenarios=tuple(scenarios), benchmark=benchmark, costs=cost_model(source),
    )
    if spec.slippage_bps >= 10_000:
        raise ValueError("slippage_bps must be below 10000")
    return spec, raw


def run_suite(spec: SuiteSpec, config_json: str, strategy_root: Path, on_scenario=None) -> dict:
    """One Static run per scenario, each tagged with the suite and scenario."""
    runs = []
    for index, item in enumerate(spec.scenarios):
        if on_scenario is not None:
            on_scenario(index, len(spec.scenarios), item.scenario.name)
        scenario = item.scenario
        run_spec = RunSpec(
            str(scenario.prices_path), spec.database_path, spec.slippage_bps,
            spec.commission_per_order, spec.cells,
            str(scenario.actions_path) if scenario.actions_path else None,
            item.symbol_map or None, costs=spec.costs,
            # Imported research data may list and delist symbols mid-run (ADR-0039).
            changing_universe=scenario.survivorship is not None,
            delistings_path=str(scenario.delistings_path) if scenario.delistings_path else None,
            membership_path=str(scenario.membership_path) if scenario.membership_path else None,
        )
        metadata = {"suite": spec.name, "scenario": scenario.summary(),
                    "symbol_map": item.symbol_map}
        run_id = run_static(run_spec, config_json, strategy_root, metadata)
        runs.append({"scenario": scenario.name, "run_id": run_id})
    return {"suite": spec.name, "database_path": spec.database_path, "runs": runs}


def _latest_runs(database: Path, suite: str) -> dict[str, tuple[str, dict]]:
    """The most recent completed run for each scenario of this suite."""
    with closing(sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro",
                                 uri=True)) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
        if "metadata_json" not in columns:
            return {}
        latest: dict[str, tuple[str, dict]] = {}
        for run_id, metadata_json in connection.execute(
            "SELECT id, metadata_json FROM runs WHERE status='completed' "
            "AND metadata_json IS NOT NULL ORDER BY created_utc"
        ):
            metadata = json.loads(metadata_json)
            if metadata.get("suite") == suite:
                latest[metadata["scenario"]["name"]] = (run_id, metadata)
        return latest


def suite_report(spec: SuiteSpec) -> dict:
    """Compare every cell on every scenario, then average by scenario tag."""
    database = Path(spec.database_path)
    if not database.is_file():
        raise FileNotFoundError(f"{database}; run the suite first")
    latest = _latest_runs(database, spec.name)
    missing = [item.scenario.name for item in spec.scenarios if item.scenario.name not in latest]
    if missing:
        raise ValueError(f"No completed run for scenario(s): {', '.join(missing)}; run the suite")
    rows = []
    for item in spec.scenarios:
        scenario = item.scenario
        run_id, metadata = latest[scenario.name]
        if metadata["scenario"]["manifest_sha256"] != scenario.manifest_sha256:
            raise ValueError(f"Scenario {scenario.name} changed since its last run; rerun the suite")
        analysis = read_analysis(database, "static", run_id, benchmark=spec.benchmark)
        for cell in analysis["cells"]:
            rows.append({
                "cell": cell["name"], "scenario": scenario.name, "kind": scenario.kind,
                "tags": list(scenario.tags), "run_id": run_id, "status": cell["status"],
                "sessions": cell["sessions"], "error": cell.get("error"),
                **{metric: cell.get(metric) for metric in METRICS},
            })
    by_tag = []
    for tag in sorted({tag for item in spec.scenarios for tag in item.scenario.tags}):
        for cell in spec.cells:
            matching = [row for row in rows if row["cell"] == cell.name and tag in row["tags"]]
            returns = [row["total_return_pct"] for row in matching
                       if row["total_return_pct"] is not None]
            drawdowns = [row["max_drawdown_pct"] for row in matching
                         if row["max_drawdown_pct"] is not None]
            by_tag.append({
                "tag": tag, "cell": cell.name, "scenarios": len(matching),
                "mean_total_return_pct": sum(returns) / len(returns) if returns else None,
                "worst_total_return_pct": min(returns) if returns else None,
                "mean_max_drawdown_pct": sum(drawdowns) / len(drawdowns) if drawdowns else None,
            })
    kinds = sorted({_caveat_kind(item.scenario) for item in spec.scenarios})
    return {
        "suite": spec.name, "benchmark": spec.benchmark,
        "scenarios": [item.scenario.summary() for item in spec.scenarios],
        "matrix": rows, "by_tag": by_tag,
        "caveats": [CAVEATS[kind] for kind in kinds],
    }


def write_suite_csv(path: Path, report: dict) -> None:
    columns = ["cell", "scenario", "kind", "tags", "status", "sessions", *METRICS, "run_id"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        for row in report["matrix"]:
            writer.writerow({**{column: row.get(column) for column in columns},
                             "tags": ";".join(row["tags"])})
