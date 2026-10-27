"""Discover every experiment, config, and scenario under one folder.

A workspace is a directory. Optional ``quantarium-workspace.json`` lists the
subfolders to scan; otherwise ``runs``, ``configs``, ``examples``, and
``scenarios`` are scanned when present. Everything is read-only, and items
are addressed by their path relative to the workspace root, which is the
identifier the HTTP API uses.
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from .legacy import WORKSPACE_FILES, current_relative, env, is_format, migrate_hidden
from .scenario import MANIFEST, load_scenario
from .scripts import strategy_classes

WORKSPACE_FILE = WORKSPACE_FILES[0]
# Where `quantarium serve --fresh` makes new, empty workspaces, unless told otherwise
# (QUANTARIUM_FRESH_ROOT, or its pre-1.0 equivalent).
FRESH_ROOT_ENV = "QUANTARIUM_FRESH_ROOT"
# Repository examples are for the command line; a new workspace starts empty
# so the first-run tutorial builds everything the user sees.
DEFAULT_FOLDERS = {"databases": ["runs"], "configs": ["configs"],
                   "scenarios": ["scenarios"], "scripts": ["strategies", "configs"],
                   "data": ["data"], "models": ["models"], "decisions": ["decisions"]}
PRICE_COLUMNS = {"date", "symbol", "open", "high", "low", "close", "volume"}


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        if not self.root.is_dir():
            raise NotADirectoryError(self.root)
        migrate_hidden(self.root)
        folders = dict(DEFAULT_FOLDERS)
        settings = next((self.root / name for name in WORKSPACE_FILES if (self.root / name).is_file()),
                        self.root / WORKSPACE_FILE)
        if settings.is_file():
            loaded = json.loads(settings.read_text(encoding="utf-8"))
            for key in DEFAULT_FOLDERS:
                if key in loaded:
                    if not isinstance(loaded[key], list):
                        raise ValueError(f"{WORKSPACE_FILE}: {key} must be a list of folders")
                    folders[key] = [str(item) for item in loaded[key]]
        self.folders = folders

    # Paths ---------------------------------------------------------------

    def resolve(self, relative: str) -> Path:
        """Map an API identifier to a path, refusing anything outside the root."""
        # Settings saved before 1.0 may name the hidden folder by its pre-1.0 name.
        path = (self.root / current_relative(relative)).resolve()
        if path != self.root and self.root not in path.parents:
            raise PermissionError(f"Path is outside the workspace: {relative}")
        return path

    def key(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def _scan(self, kind: str, pattern: str) -> list[Path]:
        found: set[Path] = set()
        for folder in self.folders[kind]:
            base = self.resolve(folder)
            if base.is_dir():
                found.update(path for path in base.rglob(pattern) if path.is_file()
                             and "test-temp" not in path.relative_to(self.root).parts
                             and not {".quantarium", ".barloom"} & set(path.relative_to(self.root).parts))
        return sorted(found)

    def folder(self, kind: str) -> Path:
        """The first configured folder of a kind, where new files are written."""
        return self.resolve(self.folders[kind][0])

    # Listings -------------------------------------------------------------

    def databases(self) -> list[dict]:
        return [self.database_summary(path) for path in self._scan("databases", "*.sqlite")]

    def database_summary(self, path: Path) -> dict:
        summary = {"key": self.key(path), "size_bytes": path.stat().st_size, "experiments": []}
        try:
            with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as db:
                tables = {row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                if "runs" in tables:
                    summary["experiments"] += _static_runs(db)
                if "feed_experiments" in tables:
                    summary["experiments"] += _feed_experiments(db)
                if "live_experiments" in tables:
                    summary["experiments"] += _live_experiments(db)
        except sqlite3.Error as exc:
            summary["error"] = f"{type(exc).__name__}: {exc}"
        return summary

    def configs(self) -> list[dict]:
        result = []
        for path in self._scan("configs", "*.json"):
            if path.name in {MANIFEST, WORKSPACE_FILE} or path.name.endswith(".manifest.json"):
                continue
            try:
                source = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            kind = config_kind(source)
            if kind is None:
                continue
            result.append({
                "key": self.key(path), "kind": kind,
                "name": source.get("name") or source.get("feed_id") or path.stem,
                "cells": [cell.get("name") for cell in source.get("cells", [])
                          if isinstance(cell, dict)],
                **({"provider": source.get("provider", "alpaca")} if kind == "feed" else {}),
            })
        return result

    def scenarios(self) -> list[dict]:
        result = []
        for manifest in self._scan("scenarios", MANIFEST):
            try:
                scenario = load_scenario(manifest.parent)
            except (ValueError, OSError) as exc:
                result.append({"key": self.key(manifest.parent),
                               "error": f"{type(exc).__name__}: {exc}"})
                continue
            result.append({"key": self.key(manifest.parent), **scenario.summary()})
        return result

    def scripts(self) -> list[dict]:
        """Strategy classes found by parsing, never importing, workspace files."""
        result = []
        from .scripts import allocator_classes

        for path in self._scan("scripts", "*.py"):
            classes, allocators = strategy_classes(path), allocator_classes(path)
            if classes or allocators:
                result.append({"key": self.key(path), "module": path.stem, "classes": classes,
                               "allocators": allocators})
        return result

    def data_files(self) -> list[dict]:
        """Static-format price files, with their symbols and date span."""
        result = []
        for path in self._scan("data", "*.csv"):
            try:
                with path.open("r", encoding="utf-8-sig", newline="") as source:
                    reader = csv.DictReader(source)
                    if not PRICE_COLUMNS <= set(reader.fieldnames or []):
                        continue
                    symbols, dates = set(), set()
                    for row in reader:
                        symbols.add(row["symbol"].strip().upper())
                        dates.add(row["date"].strip())
            except (OSError, UnicodeDecodeError, csv.Error):
                continue
            if not dates:
                continue
            manifest = path.with_name(path.name + ".manifest.json")
            result.append({"key": self.key(path), "symbols": sorted(symbols),
                           "sessions": len(dates), "start_date": min(dates),
                           "end_date": max(dates),
                           "manifest": self.key(manifest) if manifest.is_file() else None})
        return result

    def data_folders(self) -> list[dict]:
        """Research data folders in Quantarium's format, found by their source.json; read on import, not here."""
        from .userdata import FORMAT, SOURCE

        result = []
        for path in self._scan("data", SOURCE):
            try:
                source = json.loads(path.read_bytes()[:65_536])
                declared = source.get("survivorship", "unknown") if isinstance(source, dict) else None
            except (OSError, ValueError):
                continue
            if isinstance(source, dict) and is_format(source.get("format"), "data"):
                result.append({"key": self.key(path.parent), "survivorship": str(declared)[:40],
                               "files": sorted(item.name for item in path.parent.iterdir() if item.suffix == ".csv")})
        return result

    def trade_files(self) -> list[dict]:
        """CSV files with the columns of a trades file, which from-trades turns into decision files."""
        from .trades import COLUMNS

        result = []
        for path in self._scan("data", "*.csv"):
            try:
                with path.open("r", encoding="utf-8-sig", newline="") as source:
                    header = {name.strip().lower() for name in next(csv.reader(source), [])}
            except (OSError, UnicodeDecodeError, csv.Error):
                continue
            if set(COLUMNS) <= header:
                result.append({"key": self.key(path)})
        return result

    def models(self) -> list[dict]:
        from .ml.card import load_card

        result = []
        for path in self._scan("models", "risk_*.json"):
            try:
                card = load_card(path)
                result.append({"key": self.key(path), "model_id": card["model_id"],
                               "kind": card["kind"], "information_end": card["information_end"],
                               "source_status": card["data_manifest"]["source_status"]})
            except (OSError, ValueError, KeyError, TypeError) as exc:
                result.append({"key": self.key(path), "error": str(exc)})
        return result

    def decisions(self) -> list[dict]:
        """Decision files made outside Quantarium, each with its summary or what is wrong with it."""
        from .decisions import load_decisions

        result = []
        for path in sorted(self._scan("decisions", "*.json") + self._scan("decisions", "*.csv")
                           + self._scan("decisions", "*.jsonl")):
            try:
                result.append({"key": self.key(path), **load_decisions(path).summary()})
            except (OSError, ValueError) as exc:
                result.append({"key": self.key(path), "error": str(exc)})
        return result

    def overview(self) -> dict:
        return {"root": str(self.root), "folders": self.folders,
                "databases": self.databases(), "configs": self.configs(),
                "scenarios": self.scenarios(), "data_files": self.data_files(),
                "data_folders": self.data_folders(), "trade_files": self.trade_files(),
                "models": self.models(), "decisions": self.decisions()}


def config_kind(source: object) -> str | None:
    """Recognize a config by its shape, without validating it fully."""
    if not isinstance(source, dict) or not isinstance(source.get("cells"), list):
        return None
    if "scenarios" in source:
        return "suite"
    if "start_at" in source and "stop_at" in source:
        return "live"
    if "feed_id" in source and "start_date" in source:
        return "feed"
    if "data_path" in source:
        return "static"
    return None


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def _static_runs(db: sqlite3.Connection) -> list[dict]:
    metadata = "metadata_json" if "metadata_json" in _columns(db, "runs") else "NULL"
    rows = db.execute(
        f"SELECT r.id, r.created_utc, r.finished_utc, r.status, r.error, {metadata}, "
        "(SELECT COUNT(*) FROM cells c WHERE c.run_id = r.id) FROM runs r ORDER BY r.created_utc"
    ).fetchall()
    result = []
    for run_id, created, finished, status, error, labels, cells in rows:
        labels = json.loads(labels) if labels else {}
        result.append({
            "kind": "static", "id": run_id, "created_utc": created, "finished_utc": finished,
            "status": status, "error": error, "cells": cells,
            **({"suite": labels["suite"], "scenario": labels["scenario"]["name"],
                "scenario_kind": labels["scenario"]["kind"]} if "suite" in labels else {}),
        })
    return result


def _feed_experiments(db: sqlite3.Connection) -> list[dict]:
    result = []
    for feed_id, created, provider, market_feed, start in db.execute(
        "SELECT id, created_utc, provider, market_feed, start_date FROM feed_experiments "
        "ORDER BY created_utc"
    ).fetchall():
        status = "status" if "status" in _columns(db, "feed_cells") else "'active'"
        counts = dict(db.execute(
            f"SELECT {status}, COUNT(*) FROM feed_cells WHERE feed_id=? GROUP BY 1", (feed_id,)))
        last = db.execute("SELECT MAX(date) FROM feed_bars WHERE feed_id=?", (feed_id,)).fetchone()[0]
        result.append({
            "kind": "feed", "id": feed_id, "created_utc": created, "provider": provider,
            "market_feed": market_feed, "start_date": start, "last_date": last,
            "cells": sum(counts.values()), "cell_statuses": counts,
            "status": "active" if counts.get("active") else "inactive",
        })
    return result


def _live_experiments(db: sqlite3.Connection) -> list[dict]:
    return [{
        "kind": "live", "id": feed_id, "created_utc": created, "market_feed": market_feed,
        "start_at": start, "stop_at": stop, "status": status, "stop_reason": reason,
        "cells": db.execute("SELECT COUNT(*) FROM live_cells WHERE feed_id=?",
                            (feed_id,)).fetchone()[0],
    } for feed_id, created, market_feed, start, stop, status, reason in db.execute(
        "SELECT id, created_utc, market_feed, start_at, stop_at, status, stop_reason "
        "FROM live_experiments ORDER BY created_utc"
    ).fetchall()]


def fresh_workspace(parent: Path | None = None) -> Path:
    """A new, empty workspace folder, so a test starts with no cells, runs, or tutorial history."""
    parent = Path(parent or env("FRESH_ROOT") or Path.home() / "quantarium-workspaces").expanduser()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path, number = parent / f"fresh-{stamp}", 2
    while path.exists():
        path, number = parent / f"fresh-{stamp}-{number}", number + 1
    path.mkdir(parents=True)
    return path.resolve()


def is_source_checkout(path: Path) -> bool:
    """Whether a folder is Quantarium's own source, which then doubles as a workspace."""
    project = Path(path) / "pyproject.toml"
    try:
        return (Path(path) / "quantarium" / "__init__.py").is_file() and 'name = "quantarium"' in project.read_text(encoding="utf-8")
    except OSError:
        return False

