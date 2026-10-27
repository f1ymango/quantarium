"""Evidence reports: one self-contained HTML file per Lab experiment, and checking one.

The page shows what a verdict rests on: the registration and its pins, the
controls, every row, the statistics, the verdict, and every caveat. It
loads nothing from the network and runs no code; the record is embedded as
JSON data, with the SHA-256 of that exact text, so ``verify`` can check the
page against itself and against the workspace it came from. A reader
without the workspace can check only that the page is internally
consistent, not that the runs happened as shown.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path

from .legacy import is_format
from . import __version__, strictjson

FORMAT = "quantarium-evidence-report-v1"
_RECORD = re.compile(r'<script type="application/json" id="(?:quantarium|barloom)-record" data-sha256="([0-9a-f]{64})">(.*?)</script>',
                     re.DOTALL)
VERDICTS = {"supported_within_tested_scope": "Supported within the tested scope", "not_supported": "Not supported",
            "inconclusive": "Inconclusive", "unknown": "Unknown"}
LIMITS = ("A Lab result describes the named period, symbols, controls, costs, and execution model, not future returns. "
          "Its statistics assume the attempts Quantarium saw are all the attempts; tries made outside this workspace are "
          "invisible to it. Results on synthetic scenarios say nothing about real markets. This page shows what the "
          "record says; only the workspace it came from can show that the runs happened as recorded.")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _text(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.4g}" if abs(value) < 1e6 else f"{value:,.0f}"
    return html.escape(str(value))


def _table(headers: list[str], rows: list[list[object]]) -> str:
    head = "".join(f"<th>{html.escape(item)}</th>" for item in headers)
    body = "".join("<tr>" + "".join(f"<td>{_text(cell)}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def shareable(value: object, workspace_root: Path | None) -> object:
    """A record without the absolute paths of this computer: the workspace becomes ``<workspace>``, home ``~``.

    Lab records keep absolute paths for decision files, inboxes, deciders, and
    model access, which would show a stranger the user's folders and name.
    """
    if isinstance(value, dict):
        return {key: shareable(item, workspace_root) for key, item in value.items()}
    if isinstance(value, list):
        return [shareable(item, workspace_root) for item in value]
    if isinstance(value, str):
        prefixes = []
        if workspace_root is not None:
            prefixes.append((str(Path(workspace_root).resolve()), "<workspace>"))
        prefixes.append((str(Path.home()), "~"))
        for prefix, label in prefixes:
            for spelling in {prefix, prefix.replace("\\", "/")}:
                if spelling and spelling in value:
                    value = value.replace(spelling, label)
        return value
    return value


def render(record: dict, workspace_root: Path | None = None) -> str:
    """The evidence report for a Lab record, as one HTML page, without this computer's absolute paths."""
    record = shareable(record, workspace_root)
    embedded = _canonical({"format": FORMAT, "quantarium_version": __version__, "record": record})
    digest = hashlib.sha256(embedded.encode()).hexdigest()
    spec = record["spec"]
    result = record.get("result") or {}
    verdict = result.get("verdict") or {}
    statistics = verdict.get("statistics") or {}
    status = verdict.get("status")
    sections = []
    sections.append(f"""<header><p class="kicker">Quantarium evidence report</p>
<h1>{_text(spec.get("name"))}</h1>
<p>Idea <b>{_text(spec.get("idea"))}</b> · attempt {_text(record.get("attempt_number"))} · experiment {_text(record.get("id"))}
· {_text(record.get("status"))} · finished {_text(record.get("finished_utc"))}</p>
<p class="verdict v-{_text(status)}">{_text(VERDICTS.get(status, status or "Not run"))}</p></header>""")
    caveats = verdict.get("caveats") or []
    if caveats:
        sections.append("<h2>What limits this result</h2><ul>" + "".join(f"<li>{_text(item)}</li>" for item in caveats)
                        + "</ul>")
    rows = verdict.get("rows") or []
    if rows:
        sections.append("<h2>Candidate against its controls</h2>" + _table(
            ["Scenario", "Kind", "Survivorship", "From", "To", "Sessions", "Candidate %", "Best control %", "Excess pp",
             "Drawdown %", "Model memory"],
            [[row.get("scenario"), row.get("lineage") or row.get("kind"),
              (row.get("survivorship") or "n/a").replace("_", " "), row.get("start_date"), row.get("end_date"),
              row.get("sessions"), row.get("candidate_return_pct"), row.get("best_control_return_pct"),
              row.get("excess_return_pp"), row.get("drawdown_pct"), row.get("model_contamination")] for row in rows]))
    if statistics:
        sections.append("<h2>Allowing for the number of attempts</h2>" + _table(
            ["Scenario", "Sessions", "Sharpe (annual)", "Probabilistic Sharpe", "Deflated Sharpe", "Attempts",
             "Spread from"],
            [[row.get("scenario"), row.get("sessions"), row.get("sharpe_annual"), row.get("psr"), row.get("dsr"),
              row.get("trials"), row.get("variance_source") or row.get("reason")] for row in statistics.get("rows", [])])
            + f"<p>Supported needs a deflated Sharpe ratio of at least {_text(statistics.get('dsr_threshold'))} in every "
              f"scenario. Probability of backtest overfitting: {_text((statistics.get('pbo') or {}).get('pbo'))}"
              f" {('(' + _text((statistics.get('pbo') or {}).get('reason')) + ')') if (statistics.get('pbo') or {}).get('reason') else ''}.</p>")
    pins = [["Candidate cell", f"{spec.get('candidate_id')} version {spec.get('candidate_version')}"],
            ["Registration fingerprint", record.get("fingerprint")],
            ["Costs", f"{spec.get('slippage_bps')} bps slippage, {spec.get('commission_per_order')} per order"
                      + "".join(f", {name.replace('_', ' ')} {value}" for name, value in (spec.get("costs") or {}).items())],
            ["Pass rule", _canonical(spec.get("pass_rule"))]]
    pins += [[f"Script {key}", digest_] for key, digest_ in (spec.get("script_sha256") or {}).items()]
    pins += [[f"Decision file {key}", pin.get("sha256")] for key, pin in (spec.get("decision_files") or {}).items()]
    pins += [[f"Forward inbox {name}", f"{pin.get('sha256')} ({pin.get('bytes')} bytes)"]
             for name, pin in (spec.get("inboxes") or {}).items()]
    if spec.get("decider"):
        pins.append([f"Decider {spec['decider'].get('name')}", spec["decider"].get("fingerprint")])
    if spec.get("model_card_sha256"):
        pins.append(["Risk card", spec["model_card_sha256"]])
    pins += [[f"Scenario {pin.get('name')} prices", pin.get("prices_sha256")] for pin in spec.get("scenarios", [])]
    sections.append("<h2>What was frozen at registration</h2>" + _table(["Item", "Pinned as"], pins))
    sections.append(f"<h2>Limits</h2><p>{html.escape(LIMITS)}</p>")
    sections.append(f'<footer><p>Quantarium {html.escape(__version__)} · {FORMAT} · record SHA-256 <code>{digest}</code></p>'
                    "<p>Check this page with <code>quantarium lab verify-report</code> in the workspace it came from.</p></footer>")
    safe = embedded.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>{_text(spec.get("name"))} · Quantarium evidence</title>
<style>
:root {{ color-scheme: light dark; --ink: #1d1f23; --muted: #5b6270; --line: #d9dde3; --bg: #fbfbfa; --up: #1d7a46; --down: #b3261e; --wait: #8a6100; }}
@media (prefers-color-scheme: dark) {{ :root {{ --ink: #e8e9eb; --muted: #a2a8b3; --line: #343841; --bg: #16181c; --up: #5cc98b; --down: #f08a80; --wait: #e2b54f; }} }}
body {{ font: 15px/1.5 system-ui, sans-serif; color: var(--ink); background: var(--bg); max-width: 1000px; margin: 0 auto; padding: 24px 16px; }}
h1 {{ margin: 0 0 4px; }} h2 {{ margin-top: 32px; font-size: 18px; }} .kicker {{ color: var(--muted); margin: 0; text-transform: uppercase; font-size: 12px; letter-spacing: .08em; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; display: block; overflow-x: auto; }}
th, td {{ border-bottom: 1px solid var(--line); padding: 6px 8px; text-align: left; vertical-align: top; word-break: break-word; }}
.verdict {{ display: inline-block; font-weight: 700; padding: 4px 10px; border: 1px solid currentColor; border-radius: 6px; }}
.v-supported_within_tested_scope {{ color: var(--up); }} .v-not_supported {{ color: var(--down); }} .v-inconclusive, .v-unknown {{ color: var(--wait); }}
footer {{ margin-top: 40px; color: var(--muted); font-size: 12px; }} code {{ word-break: break-all; }}
</style></head><body>
{"".join(sections)}
<script type="application/json" id="quantarium-record" data-sha256="{digest}">{safe}</script>
</body></html>
"""


def verify(page: str, workspace_root: Path | None = None) -> dict:
    """Check a report: its embedded record against its stated hash, and, given a workspace, against the ledger and files."""
    from .lab import STORE

    found = _RECORD.search(page)
    if found is None:
        raise ValueError("This is not a Quantarium evidence report: its embedded record is missing")
    stated, text = found.group(1), found.group(2)
    embedded = json.dumps(strictjson.loads(text, limit=64), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    document = json.loads(embedded)
    checks = {"internally_consistent": hashlib.sha256(embedded.encode()).hexdigest() == stated
              and is_format(document.get("format"), "evidence-report")}
    record = document.get("record") or {}
    if workspace_root is not None:
        ledger = Path(workspace_root) / STORE
        items = json.loads(ledger.read_text(encoding="utf-8")) if ledger.is_file() else []
        current = next((item for item in items if item.get("id") == record.get("id")), None)
        checks["in_ledger"] = current is not None
        # Reports name paths as <workspace> and ~; those made before 1.0 kept them as they were.
        checks["matches_ledger"] = current is not None and _canonical(record) in {
            _canonical(current), _canonical(shareable(current, workspace_root))}
        changed = []
        spec = record.get("spec") or {}
        root = Path(workspace_root)
        for key, digest in (spec.get("script_sha256") or {}).items():
            path = root / key
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                changed.append(key)
        for key, pin in (spec.get("decision_files") or {}).items():
            path = root / key
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != pin.get("sha256"):
                changed.append(key)
        for pin in spec.get("scenarios", []):
            prices = root / pin.get("key", "") / "prices.csv"
            if not prices.is_file() or hashlib.sha256(prices.read_bytes()).hexdigest() != pin.get("prices_sha256"):
                changed.append(pin.get("key"))
        checks["files_unchanged"] = not changed
        checks["changed_files"] = changed
    return {"experiment": record.get("id"), "name": (record.get("spec") or {}).get("name"),
            "status": ((record.get("result") or {}).get("verdict") or {}).get("status"), **checks}
