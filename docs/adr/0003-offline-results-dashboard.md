# ADR-0003: Generate an offline results dashboard

- **Status:** Accepted for the first results interface
- **Date:** 2026-10-20
- **Deciders:** Quantarium maintainers; implementation choice recorded for user review

## Context

Quantarium saves separate cell accounts and exposes JSON summaries, equity curves, and timelines through the CLI. Comparing several cells from raw terminal output is cumbersome. The first visual interface should remain usable without credentials, a running service, or an external JavaScript package, and should open on the same laptop that runs the simulator.

## Decision

Add a `dashboard` command that reads the saved analysis in read-only mode and writes one self-contained HTML snapshot. Embed data, CSS, and JavaScript in the output file. Show the experiment state, summary metrics, indexed equity paths, and a searchable sortable cell ledger. Let the viewer show or hide cell paths. Use browser-native SVG for the chart and DOM text nodes for saved names and error messages. Escape embedded JSON so saved strategy names cannot close the data script tag.

Use all saved valuations for summary metrics. For large experiments, plot at most 500 points per cell and about 12,000 in total, retaining each time bucket's high and low and the first and last observations. Label sampling in the page. Regenerating the HTML refreshes the snapshot; the generated page never writes to SQLite.

## Alternatives considered

| Choice | Benefit | Cost or reason deferred |
| --- | --- | --- |
| Local web server that queries SQLite on each interaction | Automatic refresh and unrestricted drill-down | Adds a persistent process, local networking, and an access-control surface before the first visual view is needed |
| Notebook-based plots | Flexible for detailed research | Requires a notebook environment and makes basic cell comparisons harder to open or share |
| Chart library from a CDN | Less chart code to maintain | The dashboard would require network access or a bundled third-party asset to work offline |
| Embed every event and valuation in HTML | Full detail without another command | Large multi-cell experiments would produce slow, oversized pages |

## Consequences and follow-up

The dashboard is a point-in-time report; users rerun `dashboard` to see newer Feed results. The page contains its data, so anyone with the file can read the included cell metrics and sampled equity points. The chart is a visual summary; use `analyze --csv` and `timeline` for the full record. A later interface can add event drill-down or live refresh if actual usage warrants a local server.
