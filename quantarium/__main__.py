"""Command-line entry point for Quantarium."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from . import __version__
from .analysis import read_analysis, write_curve_csv
from .audit import audit_replay
from .config import load_config, load_feed_config, load_live_config, strategy_root
from .dashboard import write_dashboard
from .engine import run_static
from .export import export_feed_csv
from .feed import provider_for, read_feed_report, run_feed_batch
from .journal import read_report
from .live import read_live_report, run_live_feed, run_live_replay
from .live_audit import audit_live_replay
from .importer import import_bars
from .lifecycle import retire_cell
from .live_quality import inspect_live_capture
from .preflight import alpaca_preflight
from .scenario import generate_scenario, load_scenario, slice_scenario, transform_scenario
from .suite import load_suite, run_suite, suite_report, write_suite_csv
from .timeline import read_timeline, write_timeline_csv
from .userdata import import_user_data, read_user_data


def main() -> None:
    parser = argparse.ArgumentParser(prog="quantarium", description="Local portfolio strategy research simulator")
    parser.add_argument("--version", action="version", version=f"quantarium {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Replay daily CSV data through the configured cells")
    run.add_argument("config", type=Path)
    report = commands.add_parser("report", help="Show a stored run summary")
    report.add_argument("database", type=Path)
    report.add_argument("--run-id")
    batch = commands.add_parser("feed-batch", help="Fetch completed Alpaca daily bars and advance feed cells")
    batch.add_argument("config", type=Path)
    batch.add_argument("--through", type=date.fromisoformat, help="Last New York date to process (YYYY-MM-DD)")
    check = commands.add_parser(
        "alpaca-check",
        help="Check Alpaca credentials, calendar, and data coverage for a config without running it",
    )
    check.add_argument("kind", choices=("feed", "live"))
    check.add_argument("config", type=Path)
    scenario = commands.add_parser("scenario", help="Create or inspect a named input scenario")
    scenario_commands = scenario.add_subparsers(dest="scenario_command", required=True)
    cut = scenario_commands.add_parser("slice", help="Cut a date range from real daily bars")
    cut.add_argument("source", type=Path, help="Static-format price CSV, such as a Feed export")
    cut.add_argument("output", type=Path, help="Scenario directory to create")
    cut.add_argument("--from", dest="start", required=True, type=date.fromisoformat)
    cut.add_argument("--through", required=True, type=date.fromisoformat)
    cut.add_argument("--name", required=True)
    cut.add_argument("--description", default="")
    cut.add_argument("--tag", action="append", default=[], help="Label such as crash; repeatable")
    cut.add_argument("--actions", type=Path, help="Matching corporate-action CSV")
    cut.add_argument("--overwrite", action="store_true")
    generate = scenario_commands.add_parser("generate", help="Build a seeded synthetic market")
    generate.add_argument("spec", type=Path)
    generate.add_argument("output", type=Path)
    generate.add_argument("--overwrite", action="store_true")
    transform = scenario_commands.add_parser("transform", help="Alter an existing scenario")
    transform.add_argument("source", type=Path, help="Existing scenario directory")
    transform.add_argument("spec", type=Path)
    transform.add_argument("output", type=Path)
    transform.add_argument("--overwrite", action="store_true")
    show = scenario_commands.add_parser("show", help="Verify and describe a scenario")
    show.add_argument("directory", type=Path)
    data = commands.add_parser("data", help="Check or import research data in Quantarium's format")
    data_commands = data.add_subparsers(dest="data_command", required=True)
    data_check = data_commands.add_parser("check", help="Validate a quantarium-data-v1 folder and summarize it")
    data_check.add_argument("folder", type=Path)
    data_import = data_commands.add_parser("import", help="Make a historical scenario from a data folder")
    data_import.add_argument("folder", type=Path, help="A quantarium-data-v1 folder (see docs/data-format.md)")
    data_import.add_argument("output", type=Path, help="Scenario directory to create")
    data_import.add_argument("--name", required=True)
    data_import.add_argument("--description", default="")
    data_import.add_argument("--tag", action="append", default=[], help="Label such as crash; repeatable")
    data_import.add_argument("--overwrite", action="store_true")
    suite = commands.add_parser("suite", help="Run or compare one set of cells across scenarios")
    suite_commands = suite.add_subparsers(dest="suite_command", required=True)
    suite_run = suite_commands.add_parser("run", help="Run every scenario in the suite")
    suite_run.add_argument("config", type=Path)
    suite_show = suite_commands.add_parser("report", help="Compare cells across scenarios")
    suite_show.add_argument("config", type=Path)
    suite_show.add_argument("--csv", type=Path, help="Write the cells-by-scenarios matrix")
    importer = commands.add_parser(
        "import-bars", help="Convert downloaded daily price files into a Static price CSV",
    )
    importer.add_argument("output", type=Path, help="Static-format CSV to write")
    importer.add_argument("sources", nargs="+", metavar="SYMBOL=FILE",
                          help="One downloaded file per symbol, such as AAPL=AAPL.csv")
    importer.add_argument("--layout", choices=("yahoo", "nasdaq", "stooq", "plain"),
                          help="Source layout (detected from the header by default)")
    importer.add_argument("--price-basis", choices=("close", "adjusted"), default="close",
                          help="adjusted rescales Yahoo bars by Adj Close to include dividends")
    importer.add_argument("--common-dates", action="store_true",
                          help="Keep only dates every symbol has, instead of failing")
    importer.add_argument("--overwrite", action="store_true")
    ml = commands.add_parser("ml", help="Train data-only model cards in a separate process")
    ml_commands = ml.add_subparsers(dest="ml_command", required=True)
    risk = ml_commands.add_parser("train-risk", help="Train a volatility-only drop-risk baseline")
    risk.add_argument("data", type=Path, help="Static-format daily CSV")
    risk.add_argument("--output-dir", type=Path, default=Path("models"))
    risk.add_argument("--through", type=date.fromisoformat, required=True)
    risk.add_argument("--source", required=True, help="Provider or dataset name; user-declared provenance")
    risk.add_argument("--horizon", type=int, default=5)
    risk.add_argument("--drop", type=float, default=0.05)
    lab = commands.add_parser("lab", help="Evidence reports for Lab experiments")
    lab_commands = lab.add_subparsers(dest="lab_command", required=True)
    lab_report = lab_commands.add_parser("report", help="Write a self-contained HTML evidence report")
    lab_report.add_argument("experiment", help="The experiment ID, such as l_0123456789ab")
    lab_report.add_argument("--output", type=Path, help="Where to write it (default: runs/<id>-evidence.html)")
    lab_report.add_argument("--workspace", type=Path, default=Path("."))
    lab_verify = lab_commands.add_parser("verify-report", help="Check an evidence report against itself and the workspace")
    lab_verify.add_argument("report", type=Path)
    lab_verify.add_argument("--workspace", type=Path, default=None,
                            help="The workspace it came from; without it only the page itself is checked")
    decisions = commands.add_parser("decisions", help="Check decision files made outside Quantarium")
    decision_commands = decisions.add_subparsers(dest="decisions_command", required=True)
    decisions_check = decision_commands.add_parser("check", help="Validate a decision file and summarize it")
    decisions_check.add_argument("path", type=Path, help="A quantarium-decisions-v1 .json file, or a date,symbol,weight .csv")
    inbox_create = decision_commands.add_parser("inbox-create", help="Open a forward inbox for decisions")
    inbox_create.add_argument("name")
    inbox_create.add_argument("--symbols", required=True, help="Comma-separated symbols the inbox takes")
    inbox_create.add_argument("--workspace", type=Path, default=Path("."))
    submit = decision_commands.add_parser("submit", help="Submit one session's decision to a forward inbox")
    submit.add_argument("inbox")
    submit.add_argument("--date", required=True, help="The session whose close this decides for, YYYY-MM-DD")
    held = submit.add_mutually_exclusive_group(required=True)
    held.add_argument("--weights", help="Target weights such as AAA=0.5,BBB=0.3")
    held.add_argument("--hold", action="store_true", help="Keep the current holdings")
    submit.add_argument("--note", default="")
    submit.add_argument("--workspace", type=Path, default=Path("."))
    decider = decision_commands.add_parser(
        "decider-create", help="Register an outside program that decides session by session")
    decider.add_argument("name")
    decider.add_argument("--env", default="", help="Comma-separated environment variables it may receive")
    decider.add_argument("--training-cutoff", default=None, help="The last date its model was trained on")
    decider.add_argument("--workspace", type=Path, default=Path("."))
    decider.add_argument("program", nargs="*", help="The command and its arguments, after --")
    from_trades = decision_commands.add_parser("from-trades", help="Turn a trades CSV into a decision file")
    from_trades.add_argument("trades", type=Path, help="CSV with date, symbol, side, shares, price")
    from_trades.add_argument("output", type=Path, help="The decision file to write (.json)")
    from_trades.add_argument("--prices", type=Path, help="Use this price file's sessions instead of the NYSE calendar")
    from_trades.add_argument("--system", default="trades-csv", help="Where the trades came from, such as a broker")
    from_trades.add_argument("--overwrite", action="store_true")
    gap = decision_commands.add_parser("gap", help="Compare a replayed trades file's fills with the real ones")
    gap.add_argument("trades", type=Path)
    gap.add_argument("database", type=Path, help="The Static run's SQLite file")
    gap.add_argument("--cell", required=True)
    gap.add_argument("--id", default=None, help="The run; the latest run of the cell by default")
    decider_list = decision_commands.add_parser("deciders", help="List registered deciders")
    decider_list.add_argument("--workspace", type=Path, default=Path("."))
    serve_command = commands.add_parser("serve", help="Run the local JSON API and frontend")
    serve_command.add_argument("--workspace", type=Path, default=None,
                               help="Folder to discover experiments in (default: current)")
    serve_command.add_argument("--fresh", action="store_true",
                               help="Serve a new, empty workspace: no cells, runs, or tutorial history")
    serve_command.add_argument("--fresh-root", type=Path, default=None,
                               help="Where --fresh makes its folder (default: ~/quantarium-workspaces, "
                                    "or QUANTARIUM_FRESH_ROOT)")
    serve_command.add_argument("--host", default="127.0.0.1", help="Loopback address to bind")
    serve_command.add_argument("--port", type=int, default=8765)
    agents_command = commands.add_parser(
        "agents", help="Show, turn on, or turn off agent features for a workspace (off by default)")
    agents_command.add_argument("action", choices=("status", "enable", "disable"))
    agents_command.add_argument("--workspace", type=Path, default=Path("."),
                                help="Workspace folder (default: current)")
    mcp_command = commands.add_parser(
        "mcp", help="Serve Quantarium to an AI agent over MCP on stdio (needs agent features on)")
    mcp_command.add_argument("--workspace", type=Path, default=Path("."),
                             help="Workspace folder (default: current)")
    retire = commands.add_parser(
        "retire-cell", help="Stop one Feed Batch or Live Feed cell; the others continue",
    )
    retire.add_argument("kind", choices=("feed", "live"))
    retire.add_argument("database", type=Path)
    retire.add_argument("feed_id")
    retire.add_argument("cell")
    retire.add_argument("--reason", help="Note saved with the cell_retired event")
    feed_report = commands.add_parser("feed-report", help="Show a stored feed experiment summary")
    feed_report.add_argument("database", type=Path)
    feed_report.add_argument("feed_id")
    live = commands.add_parser("live-feed", help="Simulate cells from synchronized Alpaca minute bars")
    live.add_argument("config", type=Path)
    live.add_argument("--capture", type=Path,
                      help="Create a timestamped JSONL capture of market frames and clock ticks")
    live_replay = commands.add_parser("live-replay", help="Replay timestamped Alpaca minute frames")
    live_replay.add_argument("config", type=Path)
    live_replay.add_argument("frames", type=Path)
    live_report = commands.add_parser("live-report", help="Show a stored Live Feed experiment summary")
    live_report.add_argument("database", type=Path)
    live_report.add_argument("feed_id")
    live_audit = commands.add_parser("audit-live-replay",
                                     help="Compare a Live Feed run with captured replay")
    live_audit.add_argument("source_database", type=Path)
    live_audit.add_argument("source_feed_id")
    live_audit.add_argument("capture", type=Path)
    live_audit.add_argument("replay_database", type=Path)
    live_audit.add_argument("replay_feed_id")
    quality = commands.add_parser("capture-quality",
                                  help="Inspect coverage and timing of Live Feed frames")
    quality.add_argument("config", type=Path)
    quality.add_argument("frames", type=Path)
    feed_export = commands.add_parser("export-feed", help="Export saved Feed bars for Static replay")
    feed_export.add_argument("database", type=Path)
    feed_export.add_argument("feed_id")
    feed_export.add_argument("output", type=Path)
    feed_export.add_argument("--through", type=date.fromisoformat,
                             help="Last saved date to include (YYYY-MM-DD)")
    feed_export.add_argument("--overwrite", action="store_true",
                             help="Replace existing export files")
    audit = commands.add_parser("audit-replay", help="Compare saved Feed and Static event trails")
    audit.add_argument("feed_database", type=Path)
    audit.add_argument("feed_id")
    audit.add_argument("static_database", type=Path)
    audit.add_argument("--run-id", help="Static run ID (defaults to latest completed)")
    analyze = commands.add_parser("analyze", help="Compare saved cell performance and optionally export curves")
    analyze.add_argument("kind", choices=("static", "feed", "live"))
    analyze.add_argument("database", type=Path)
    analyze.add_argument("--id", help="Static run ID (defaults to latest completed) or required Feed ID")
    analyze.add_argument("--benchmark", help="Cell name to use as the comparison baseline")
    analyze.add_argument("--csv", type=Path, help="Write per-cell daily equity curves to a CSV file")
    dashboard = commands.add_parser("dashboard", help="Build a standalone results dashboard")
    dashboard.add_argument("kind", choices=("static", "feed", "live"))
    dashboard.add_argument("database", type=Path)
    dashboard.add_argument("--id", help="Static run ID (defaults to latest completed) or required Feed ID")
    dashboard.add_argument("--benchmark", help="Cell name to compare against")
    dashboard.add_argument("--output", required=True, type=Path, help="HTML snapshot path")
    timeline = commands.add_parser("timeline", help="Inspect one cell's saved daily events")
    timeline.add_argument("kind", choices=("static", "feed", "live"))
    timeline.add_argument("database", type=Path)
    timeline.add_argument("cell")
    timeline.add_argument("--id", help="Static run ID (defaults to latest) or required Feed ID")
    timeline.add_argument("--from", dest="start",
                          help="First date or UTC minute to include")
    timeline.add_argument("--through", help="Last date or UTC minute to include")
    timeline.add_argument("--csv", type=Path, help="Write daily decisions and fills to a CSV file")
    args = parser.parse_args()
    if args.command == "run":
        spec, raw = load_config(args.config)
        run_id = run_static(spec, raw, strategy_root(args.config, raw))
        print(json.dumps(read_report(Path(spec.database_path), run_id), indent=2))
    elif args.command == "report":
        print(json.dumps(read_report(args.database, args.run_id), indent=2))
    elif args.command == "feed-batch":
        spec, raw = load_feed_config(args.config)
        provider = provider_for(spec)
        print(json.dumps(run_feed_batch(
            spec, raw, strategy_root(args.config, raw), provider, through=args.through
        ), indent=2))
    elif args.command == "alpaca-check":
        loader = load_feed_config if args.kind == "feed" else load_live_config
        spec, _ = loader(args.config)
        if spec.provider != "alpaca":
            raise SystemExit("alpaca-check applies only to Alpaca configs")
        symbols = {symbol for cell in spec.cells for symbol in cell.symbols}
        result = alpaca_preflight(args.kind, symbols, spec.market_feed)
        print(json.dumps(result, indent=2))
        if not result["ready"]:
            raise SystemExit(1)
    elif args.command == "scenario":
        if args.scenario_command == "slice":
            result = slice_scenario(args.source, args.output, args.start, args.through,
                                    name=args.name, description=args.description,
                                    tags=tuple(args.tag), actions=args.actions,
                                    overwrite=args.overwrite)
        elif args.scenario_command == "generate":
            spec = json.loads(args.spec.read_text(encoding="utf-8"))
            result = generate_scenario(spec, args.output, overwrite=args.overwrite)
        elif args.scenario_command == "transform":
            spec = json.loads(args.spec.read_text(encoding="utf-8"))
            result = transform_scenario(args.source, spec, args.output, overwrite=args.overwrite)
        else:
            result = load_scenario(args.directory)
        print(json.dumps(result.summary(), indent=2))
    elif args.command == "data":
        if args.data_command == "check":
            print(json.dumps(read_user_data(args.folder).summary(), indent=2))
        else:
            scenario, warnings = import_user_data(args.folder, args.output, name=args.name,
                                                  description=args.description, tags=tuple(args.tag),
                                                  overwrite=args.overwrite)
            print(json.dumps({**scenario.summary(), "survivorship": scenario.survivorship,
                              "warnings": warnings}, indent=2))
    elif args.command == "suite":
        spec, raw = load_suite(args.config)
        if args.suite_command == "run":
            print(json.dumps(run_suite(spec, raw, strategy_root(args.config, raw)), indent=2))
        else:
            result = suite_report(spec)
            if args.csv:
                write_suite_csv(args.csv, result)
            print(json.dumps(result, indent=2))
    elif args.command == "import-bars":
        sources = {}
        for item in args.sources:
            symbol, separator, file = item.partition("=")
            if not separator or not symbol or not file:
                raise SystemExit(f"Expected SYMBOL=FILE, got {item}")
            sources[symbol] = Path(file)
        print(json.dumps(import_bars(sources, args.output, layout=args.layout,
                                     price_basis=args.price_basis, overwrite=args.overwrite,
                                     common_dates=args.common_dates), indent=2))
    elif args.command == "ml":
        environment = {key: value for key, value in os.environ.items() if key.upper() in {
            "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "LANG", "LC_ALL",
        }}
        command = [sys.executable, "-m", "quantarium.ml.train_risk", str(args.data),
                   "--output-dir", str(args.output_dir), "--through", args.through.isoformat(),
                   "--source", args.source, "--horizon", str(args.horizon), "--drop", str(args.drop)]
        completed = subprocess.run(command, env=environment, text=True, capture_output=True, check=False)
        if completed.returncode:
            raise SystemExit(completed.stderr.strip() or f"ML training failed ({completed.returncode})")
        print(json.dumps(json.loads(completed.stdout), indent=2))
    elif args.command == "lab":
        from .evidence import render, verify

        if args.lab_command == "report":
            from .lab import Lab
            from .workspace import Workspace

            root = args.workspace.resolve()
            record = Lab(Workspace(root), None).get(args.experiment)
            output = args.output or root / "runs" / f"{args.experiment}-evidence.html"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(render(record, root), encoding="utf-8")
            print(json.dumps({"experiment": args.experiment, "report": str(output)}, indent=2))
        else:
            try:
                result = verify(args.report.read_text(encoding="utf-8"),
                                args.workspace.resolve() if args.workspace else None)
            except (OSError, ValueError) as exc:
                raise SystemExit(f"Not a valid evidence report: {exc}")
            print(json.dumps(result, indent=2))
            if not all(result[key] for key in ("internally_consistent", "in_ledger", "matches_ledger") if key in result):
                raise SystemExit(1)
    elif args.command == "decisions":
        from . import inbox
        from .decisions import load_decisions

        try:
            if args.decisions_command == "check":
                print(json.dumps(load_decisions(args.path).summary(), indent=2))
            elif args.decisions_command == "from-trades":
                from .trades import write_decisions

                print(json.dumps(write_decisions(args.trades, args.output, prices=args.prices, system=args.system,
                                                 overwrite=args.overwrite), indent=2))
            elif args.decisions_command == "gap":
                from .trades import fill_gap

                print(json.dumps(fill_gap(args.trades, args.database, args.cell, args.id), indent=2))
            elif args.decisions_command == "decider-create":
                from . import deciders

                program = args.program
                print(json.dumps(deciders.create(args.workspace.resolve(), {
                    "name": args.name, "command": program, "training_cutoff": args.training_cutoff,
                    "env": [item.strip() for item in args.env.split(",") if item.strip()]}), indent=2))
            elif args.decisions_command == "deciders":
                from . import deciders

                print(json.dumps(deciders.listing(args.workspace.resolve()), indent=2))
            elif args.decisions_command == "inbox-create":
                symbols = [item.strip() for item in args.symbols.split(",") if item.strip()]
                print(json.dumps(inbox.create(args.workspace.resolve(), args.name, symbols, "user"), indent=2))
            else:
                weights = None
                if args.weights:
                    weights = {}
                    for item in args.weights.split(","):
                        symbol, _, value = item.partition("=")
                        weights[symbol.strip()] = float(value)
                print(json.dumps(inbox.submit(args.workspace.resolve(), args.inbox, {
                    "date": args.date, "weights": weights, "note": args.note}, "user"), indent=2))
        except (OSError, ValueError) as exc:
            raise SystemExit(f"Decision refused: {exc}" if args.decisions_command != "check"
                             else f"Invalid decision file: {exc}")
    elif args.command == "serve":
        from .server import serve
        from .workspace import fresh_workspace, is_source_checkout

        if args.fresh and args.workspace is not None:
            raise SystemExit("Use --fresh-root, not --workspace, to choose where --fresh makes its folder")
        if args.fresh:
            workspace = fresh_workspace(args.fresh_root)
            print(f"Fresh workspace: {workspace}")
            print("It is yours to keep or delete when you are done.")
        else:
            workspace = args.workspace or Path(".")
            if is_source_checkout(workspace.resolve()):
                print("Note: this is Quantarium's source folder, so it is also serving as the workspace. Cells, runs, "
                      "scenarios, and tutorial progress you make here stay here. For a clean start, run: "
                      "py -m quantarium serve --fresh", file=sys.stderr)
        serve(workspace, args.host, args.port)
    elif args.command == "agents":
        from .agents import run_cli
        from .workspace import Workspace
        raise SystemExit(run_cli(args.action, Workspace(args.workspace)))
    elif args.command == "mcp":
        from .mcp import main as mcp_main
        raise SystemExit(mcp_main(args.workspace))
    elif args.command == "retire-cell":
        print(json.dumps(retire_cell(args.kind, args.database, args.feed_id, args.cell,
                                     args.reason), indent=2))
    elif args.command == "feed-report":
        print(json.dumps(read_feed_report(args.database, args.feed_id), indent=2))
    elif args.command == "live-feed":
        spec, raw = load_live_config(args.config)
        print(json.dumps(asyncio.run(run_live_feed(
            spec, raw, strategy_root(args.config, raw), capture_path=args.capture
        )), indent=2))
    elif args.command == "live-replay":
        spec, raw = load_live_config(args.config)
        print(json.dumps(run_live_replay(
            spec, raw, strategy_root(args.config, raw), args.frames
        ), indent=2))
    elif args.command == "live-report":
        print(json.dumps(read_live_report(args.database, args.feed_id), indent=2))
    elif args.command == "audit-live-replay":
        result = audit_live_replay(args.source_database, args.source_feed_id,
                                   args.capture, args.replay_database,
                                   args.replay_feed_id)
        print(json.dumps(result, indent=2))
        if not result["matched"]:
            raise SystemExit(1)
    elif args.command == "capture-quality":
        spec, _ = load_live_config(args.config)
        print(json.dumps(inspect_live_capture(spec, args.frames), indent=2))
    elif args.command == "dashboard":
        analysis = read_analysis(args.database, args.kind, args.id,
                                 benchmark=args.benchmark)
        print(json.dumps(write_dashboard(args.output, analysis), indent=2))
    elif args.command == "export-feed":
        print(json.dumps(export_feed_csv(args.database, args.feed_id, args.output,
                                         args.through, overwrite=args.overwrite), indent=2))
    elif args.command == "audit-replay":
        result = audit_replay(args.feed_database, args.feed_id,
                              args.static_database, args.run_id)
        print(json.dumps(result, indent=2))
        if not result["matched"]:
            raise SystemExit(1)
    elif args.command == "timeline":
        parse_time = ((lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")))
                      if args.kind == "live" else date.fromisoformat)
        result = read_timeline(args.database, args.kind, args.cell, args.id,
                               start=parse_time(args.start) if args.start else None,
                               through=parse_time(args.through) if args.through else None)
        if args.csv is not None:
            write_timeline_csv(args.csv, result)
        print(json.dumps(result, indent=2))
    else:
        result = read_analysis(args.database, args.kind, args.id, benchmark=args.benchmark)
        if args.csv is not None:
            write_curve_csv(args.csv, result)
        print(json.dumps({key: value for key, value in result.items() if key != "curve"}, indent=2))


if __name__ == "__main__":
    main()

