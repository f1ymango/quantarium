"""Build a self-contained, read-only HTML snapshot of saved cell performance."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path


MAX_CHART_POINTS_PER_CELL = 500
MAX_CHART_POINTS_TOTAL = 12_000


def _sample_curve(points: list[dict], limit: int = MAX_CHART_POINTS_PER_CELL) -> list[dict]:
    """Keep the endpoints and each time bucket's local high and low."""
    if len(points) <= limit:
        return points
    if limit < 4:
        raise ValueError("Chart sample limit must be at least four")
    interior = points[1:-1]
    buckets = (limit - 2) // 2
    sampled = [points[0]]
    for bucket in range(buckets):
        start = bucket * len(interior) // buckets
        end = (bucket + 1) * len(interior) // buckets
        segment = interior[start:end]
        if not segment:
            continue
        low = min(range(len(segment)), key=lambda index: segment[index]["index_100"])
        high = max(range(len(segment)), key=lambda index: segment[index]["index_100"])
        sampled.extend(segment[index] for index in sorted({low, high}))
    sampled.append(points[-1])
    return sampled


def write_dashboard(path: Path, analysis: dict) -> dict:
    """Write an offline dashboard from a read_analysis result."""
    asset_root = files("quantarium").joinpath("assets")
    template = asset_root.joinpath("dashboard.html").read_text(encoding="utf-8")
    css = asset_root.joinpath("dashboard.css").read_text(encoding="utf-8")
    script = asset_root.joinpath("dashboard.js").read_text(encoding="utf-8")
    by_cell: dict[str, list[dict]] = {cell["name"]: [] for cell in analysis["cells"]}
    for point in analysis["curve"]:
        by_cell[point["cell"]].append(point)
    sampled = []
    per_cell_limit = max(4, min(MAX_CHART_POINTS_PER_CELL,
                                MAX_CHART_POINTS_TOTAL // max(1, len(analysis["cells"]))))
    for cell in analysis["cells"]:
        sampled.extend(_sample_curve(by_cell[cell["name"]], per_cell_limit))
    payload = {
        "kind": analysis["kind"],
        "experiment_id": analysis["experiment_id"],
        "status": analysis["status"],
        "benchmark": analysis["benchmark"],
        "cells": analysis["cells"],
        "curve": sampled,
        "full_curve_rows": len(analysis["curve"]),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    embedded = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":"))
    embedded = (embedded.replace("&", "\\u0026").replace("<", "\\u003c")
                .replace(">", "\\u003e"))
    replacements = {"CSS": css, "JS": script, "DATA": embedded}
    html = re.sub(r"__QUANTARIUM_(CSS|JS|DATA)__",
                  lambda match: replacements[match.group(1)], template)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return {
        "output": str(path.resolve()),
        "cells": len(analysis["cells"]),
        "full_curve_rows": len(analysis["curve"]),
        "chart_rows": len(sampled),
    }
