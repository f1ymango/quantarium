"""Compact summaries of results, sized for an AI model's context window.

A summary keeps the headline metrics, says how much data stands behind them,
and states caveats in words: too few trades, a short history, a result
carried by a single session, missing costs, or synthetic data. Agents read
the caveats with the numbers, so they reach the model and not only the UI.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .model import COST_FIELDS

CELL_FIELDS = ("name", "mode", "status", "error", "sessions", "minutes", "total_return_pct",
               "max_drawdown_pct", "sharpe_ratio_rf0", "annualized_volatility_pct",
               "average_exposure_pct", "turnover_ratio", "orders_filled", "excess_return_pp",
               "forward_sessions", "forward_return_pct")
MIN_SESSIONS = 60
MIN_FILLS = 4
# A session that earned more than this share of the total return carries the result.
ONE_SESSION_SHARE = 0.5


def _round(value):
    return round(value, 4) if isinstance(value, float) else value


def _static_costs(path: Path, run_id: str | None) -> dict | None:
    """Slippage and commission a Static run used, read from its saved config."""
    if not run_id:
        return None
    try:
        with closing(sqlite3.connect(path)) as connection:
            row = connection.execute("SELECT config_json FROM runs WHERE id=?", (run_id,)).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    config = json.loads(row[0])
    costs = {"slippage_bps": config.get("slippage_bps", 0),
             "commission_per_order": config.get("commission_per_order", 0)}
    return {**costs, **{name: config[name] for name in COST_FIELDS if config.get(name)}}


def _largest_session_share(curve: list[dict]) -> float | None:
    """The best single session's gain as a share of the cell's total gain."""
    indexes = [point["index_100"] for point in curve if point.get("index_100") is not None]
    if len(indexes) < 2 or indexes[-1] <= indexes[0]:
        return None
    gains = [after - before for before, after in zip(indexes, indexes[1:])]
    return max(gains) / (indexes[-1] - indexes[0])


def summarize_analysis(analysis: dict, path: Path) -> dict:
    """The analysis without its curve, with per-cell and experiment caveats."""
    run_id = analysis.get("experiment_id")
    costs = _static_costs(path, run_id) if analysis["kind"] == "static" else None
    curves: dict[str, list[dict]] = {}
    for point in analysis.get("curve", []):
        curves.setdefault(point["cell"], []).append(point)
    cells, caveats = [], []
    for cell in analysis["cells"]:
        entry = {key: _round(cell.get(key)) for key in CELL_FIELDS if cell.get(key) is not None}
        warnings = []
        periods = cell.get("sessions", cell.get("minutes")) or 0
        if cell.get("status") not in {"completed", "active"}:
            warnings.append(f"Cell is {cell.get('status')}; its results stop early")
        if analysis["kind"] != "live" and periods < MIN_SESSIONS:
            warnings.append(f"Only {periods} sessions; too short to judge")
        fills = cell.get("orders_filled") or 0
        if fills < MIN_FILLS:
            warnings.append(f"Only {fills} filled order{'' if fills == 1 else 's'}; "
                            "the result rests on very few decisions")
        share = _largest_session_share(curves.get(cell["name"], []))
        if share is not None and share > ONE_SESSION_SHARE:
            warnings.append(f"One session produced {share:.0%} of the gain")
        if warnings:
            entry["warnings"] = warnings
        cells.append(entry)
    if costs is not None and not any(costs.values()):
        caveats.append("No slippage or commission was modeled; real trading costs lower returns")
    if any(cell.get("input") == "scenario" for cell in analysis["cells"]):
        caveats.append("Fed from a scenario, not live market data")
    return {"kind": analysis["kind"], "experiment_id": run_id, "status": analysis["status"],
            "benchmark": analysis.get("benchmark"), "costs": costs, "cells": cells,
            "caveats": caveats, "curve_points": len(analysis.get("curve", []))}


def summarize_suite(report: dict) -> dict:
    """Per-cell results across scenarios, with the suite's caveats."""
    by_cell: dict[str, list[dict]] = {}
    for row in report["matrix"]:
        by_cell.setdefault(row["cell"], []).append(row)
    cells = []
    for name, rows in by_cell.items():
        returns = [row["total_return_pct"] for row in rows if row["total_return_pct"] is not None]
        drawdowns = [row["max_drawdown_pct"] for row in rows if row["max_drawdown_pct"] is not None]
        worst = min(rows, key=lambda row: row["total_return_pct"]
                    if row["total_return_pct"] is not None else float("inf"))
        cells.append({
            "cell": name, "scenarios": len(rows),
            "failed": sum(1 for row in rows if row["status"] not in {"completed"}),
            "mean_total_return_pct": _round(sum(returns) / len(returns)) if returns else None,
            "worst_total_return_pct": _round(min(returns)) if returns else None,
            "worst_scenario": worst["scenario"] if returns else None,
            "mean_max_drawdown_pct": _round(sum(drawdowns) / len(drawdowns)) if drawdowns else None,
        })
    caveats = list(report.get("caveats", []))
    if len(report["scenarios"]) == 1:
        caveats.append("Only one scenario; a result on one market shape says little about others")
    return {"suite": report["suite"], "benchmark": report.get("benchmark"),
            "scenarios": [{"name": item.get("name"), "kind": item.get("kind"), "tags": item.get("tags")}
                          for item in report["scenarios"]],
            "cells": cells, "caveats": caveats}


def page_curve(analysis: dict, offset: object, limit: object) -> dict:
    """Return one page of the analysis curve, with the total number of points."""
    curve = analysis.get("curve", [])
    start = max(0, int(offset or 0))
    count = len(curve) - start if limit in (None, "") else max(0, min(int(limit), 5000))
    return {**analysis, "curve": curve[start:start + count], "curve_offset": start,
            "curve_total": len(curve)}
