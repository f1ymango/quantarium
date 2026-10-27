# Quantarium local API, v1

Start the server from a workspace folder:

```shell
python -m quantarium serve --workspace . --port 8765
```

All endpoints live under `http://127.0.0.1:8765/api/v1/` and return JSON. Paths in parameters (`db`, `config`, `key`, `source`, `output`) are relative to the workspace root and must stay inside it. Errors return `{"error": message, "status": http_status, "code": code}`, plus a `hint` saying what to do next when Quantarium has one. Branch on `code`; messages may change wording.

| Status | Meaning |
| --- | --- |
| 400 | Invalid parameter |
| 401 | Unknown or revoked agent token |
| 403 | Path outside the workspace, a write without the token, or an agent request that isn't permitted |
| 404 | Unknown endpoint, action, or file |
| 409 | An idempotency key reused for a different request |
| 415 | Write body is not JSON |
| 421 | Foreign `Host` header |
| 429 | An agent connection's budget is used up |

| Code | Meaning |
| --- | --- |
| `missing_parameter`, `invalid_parameter`, `invalid_request` | The request is malformed or a value is wrong |
| `unknown_endpoint`, `unknown_command`, `unknown_action`, `unknown_job` | The name isn't one Quantarium knows |
| `not_found`, `cell_not_found` | A file or cell the request names doesn't exist |
| `name_taken` | Another cell already has that name |
| `cell_not_ready` | A cell has configuration problems; the message lists them |
| `cell_has_results` | Cells with results can be archived, not deleted |
| `already_in_experiment`, `feed_settings_changed` | A Feed deploy conflicts with the experiment; use a new `feed_id` |
| `idempotency_conflict` | See idempotency keys under [Writes](#writes) |
| `lab_not_found`, `lab_already_run` | Unknown Lab registration, or one that has already started |
| `agents_disabled`, `agents_paused`, `unknown_token`, `not_permitted`, `budget_exceeded` | See [Agent features](#agent-features) |
| `forbidden`, `unexpected_host`, `unsupported_media_type`, `too_large`, `internal_error` | Transport and server errors |

A failed job's `error` has the same `code` and `hint` (`job_failed` when the failure carries no code of its own), with `type`, `message`, and `trace`.

The design and security model are in [ADR-0011](adr/0011-local-api-and-frontend-architecture.md).

## First-run tutorial

`GET onboarding` returns the workspace's tutorial state: `status` (`pending`, `active`, `completed`, or `skipped`), `step`, `cell_id`, `names` (the scenario, cell, Feed, script, and database names this run uses), and `starter_script` once `strategies/starter.py` exists. The app shows the tutorial's title screen while the status is `pending`. Commands:

| Command | Params | Effect |
| --- | --- | --- |
| `onboarding-start` | | Starts or replays the tutorial at step 0 in a new, empty tutorial workspace, with names that don't collide with anything in the workspace |
| `onboarding-step` | `step`, optional `cell_id` | Records progress while the tutorial is active |
| `onboarding-finish` | `status` (`completed` or `skipped`) | Ends the tutorial. `completed` (which needs a recorded cell) copies the tutorial's files into the workspace at the same paths; `skipped` discards them. Both copy the starter scripts if absent |

While the status is `active`, every other read, command, and job is served from a private tutorial workspace at `.quantarium/tutorial/`, which starts empty and uses the default scan folders. The tutorial therefore works through the normal endpoints, and clients see only its content. State is stored in `.quantarium/onboarding.json`; an `active` state whose tutorial folder is missing (for example, one from v0.2.1) reads as `pending`.

## Agent features

Agent features are off by default ([ADR-0014](adr/0014-agent-connections-and-permissions.md)). `GET agents` returns `enabled`, `notice_version`, `accepted_version`, `accepted_utc`, `needs_new_acceptance`, the `confirmation` phrase, and the `notice` text. Commands:

| Command | Params | Effect |
| --- | --- | --- |
| `agents-enable` | `confirmation` (the phrase, typed by the user), `notice_version` (the version shown) | Turns agent features on; refused if the phrase or version doesn't match |
| `agents-disable` | | Turns agent features off |

While agent features are off, agent-facing requests fail with `403` and `"code": "agents_disabled"` in the error body. The setting belongs to the real workspace, also while the tutorial runs.

**Assistant connections.** `agent-connection-create` also takes `kind`:

- `external` (the default) returns a token.
- `direct` (a model API, [ADR-0021](adr/0021-model-api-port.md)) takes `provider`: `{"preset": ..., "protocol": "openai-chat" | "anthropic-messages", "endpoint": URL, "model": ..., "key_env": "VAR"}`, with `"key_store": "keyring"` or `"key_store": "none"` (a local server) in place of `key_env`. A preset fills in `protocol` and `endpoint`; `custom` needs both. Endpoints must use HTTPS except on this computer. The v0.3.0 form `{"provider": "anthropic" | "openai", ...}` is still accepted. It has no token.
- `bridge` (an agent app, [ADR-0022](adr/0022-agent-runtime-port.md)) takes `provider`: `{"runtime": "codex" | "claude-code" | "custom-acp"}`. `custom-acp` also takes `command`, the agent app and its arguments as a list. A runtime with an acknowledgement (`claude-code`) needs `acknowledgement` set to its exact text; the connection records `acknowledged_utc`. The v0.3.0 form `{"bridge": "codex"}` is still accepted.

App-only commands for model API connections:

| Command | Params | Effect |
| --- | --- | --- |
| `agent-connection-key` | `id`, `key` | Stores the key in the system keychain. Keys are never saved by Quantarium, logged, or returned |
| `agent-connection-test` | `id` | Sends one request with one `ping` tool; returns `ok`, `tool_calls`, `seconds`, and a `message` |
| `agent-connection-models` | `id` | Returns `{models}`, from the endpoint's `GET /models` |
| `agent-connection-model` | `id`, `model` | Changes the connection's model, which clears its training cutoff |
| `agent-connection-cutoff` | `id`, `training_cutoff` (an ISO date, or null to clear) | Records the model's training cutoff as its provider discloses it; the Lab uses it to judge model memory |

`GET agents` adds `assistant`, which reports whether the SDKs and `keyring` are installed and, in `apps`, which agent apps are on PATH, and `presets`, the contents of `presets.json`.

**Chats** (app only; [ADR-0019](adr/0019-in-app-assistant-and-model-connections.md)):

- `GET agents/chats` lists chats.
- `GET agents/chats/{id}` returns one chat, with `messages` (blocks of `text`, `tool_use`, and `tool_result`, plus any provider blocks), `status`, `running`, and `usage`. For an agent app chat it adds `app_profile` and `app_transcripts`, the app's own transcript files Quantarium found.
- Commands:
  - `chat-create` (`connection_id`);
  - `chat-send` (`id`, `text`, optional `page`), which starts the model's turn in the background;
  - `chat-stop`, `chat-delete`, and `chat-rename` (`id`, plus `title` for renaming). `chat-delete` takes `app_transcript: true` to also delete the agent app's transcript (`codex_transcript` is the v0.3.0 name).

The model's tool calls go through the agent path as that connection. With a `bridge` connection, each turn runs the agent app with `quantarium mcp` and a token valid for that turn only ([ADR-0022](adr/0022-agent-runtime-port.md)); the chat stores the app's session in `bridge` (`thread_id` for Codex, `session_id` for Claude Code and ACP agents).

**Connections.** The app manages them, and so does any client with the browser token; agent tokens cannot. `GET agents` lists connections without their token digests.

| Command | Params | Effect |
| --- | --- | --- |
| `agent-connection-create` | `name`, optional `tiers` (default `observe`, `draft`, `simulate`, `propose`) | Returns `{connection, token}`; the token is shown only here |
| `agent-connection-revoke` | `id` | The token stops working at once |

**Agent requests** use the same endpoints with `Authorization: Bearer <token>` in place of the browser token. They need agent features on, an active connection, and the tier the operation requires:

| Tier | Operations |
| --- | --- |
| `observe` | `GET` health, workspace, analysis, timeline, report, config, scenario, suite-report, jobs, `jobs/{id}`, cells, `cells/{id}`, scripts, `scripts/{key}`, holdings, arena, lab, `lab/{id}`, agent-log, decision-inboxes; command `script-check` |
| `draft` | Commands `cell-create`, `cell-save`, `cell-duplicate`, `script-write` (only under `strategies/agent/` unless the connection was created with `edit_user_scripts`) |
| `simulate` | Jobs `scenario-generate`, `scenario-transform`, `scenario-slice`, `run-static`, `run-suite`, `deploy-static`, `deploy-suite`; commands `decision-inbox-create`, `decision-submit` (at most `decisions_per_day`, 500 by default) |
| `propose` | Command `proposal-create`; reading its own proposals with `GET proposals` is `observe` |
| `manage` | Commands `cell-archive`, `cell-restore`; jobs `feed-batch`, `retire-cell` |

Agent tokens can also read their own log entries with `GET agent-log` (`observe`). Everything else is refused for agent tokens with `403` and `"code": "not_permitted"`. An unknown or revoked token gets `401` and `"code": "unknown_token"`.

**Scripts agents write never run unreviewed.** Every script version an agent writes is recorded by content hash in `.quantarium/script-provenance.json` before the file is saved. The strategy loader refuses to run any recorded version, however the run was started, with `script_unreviewed`. A user's own edit changes the hash, so the edited file runs as the user's code. Agents cannot write `__init__.py`.

To let an agent's version run, the user reviews it:

- `GET agents/scripts` lists recorded versions with `key`, `sha256`, `state` (`agent-unreviewed` or `reviewed`), the connection that wrote them, and `current` (whether the file still has that content).
- The command `script-review` with `sha256` marks a version reviewed.
- Agent tokens can use neither.
- `GET scripts` marks each listed script with `agent_unreviewed`.

Until a version is reviewed, Static runs and suites execute it in a sandbox ([ADR-0016](adr/0016-running-agent-written-strategies.md)): a separate, isolated process with a code policy, an audit hook that refuses files, sockets, processes, and imports, resource limits, and timeouts. Failures there have code `sandbox_policy` or `sandbox_failed`. Feed and Live refuse unreviewed versions with `script_unreviewed`. `script-check` adds `sandbox_problems`, which must be empty for a script to run in the sandbox.

**Proposals** ([ADR-0017](adr/0017-proposals-and-approvals.md)).

- **Filing.** Agents with `propose` file one with the command `proposal-create`: `action` (`deploy-feed`, `feed-batch`, `retire-cell`, `cell-archive`, `cell-restore`, `holdout-test`, or `lab-register`), `params` (the same as acting directly), a `rationale`, and optional `evidence`. Filing checks the parameters without running anything, and counts against `proposals_per_day` (default 20). Lab proposals pin the scenario data as well as the cell and scripts.
- **Contents.** A proposal has an `id`, a `status` (`pending`, `approved`, `rejected`, `stale`, or `failed`), and `pins`: a fingerprint of each named cell and the SHA-256 of each script those cells use.
- **Listing.**
  - `GET proposals` with an agent token lists that connection's own.
  - `GET agents/proposals` (optional `status`) lists all, each with `scripts` showing whether each pinned version is agent-written and unreviewed.
- **Deciding.** The command `proposal-decide` takes `id`, `decision` (`approve` or `reject`), and optional `note`, and only the app may call it.
  - Approving refuses with `proposal_stale` if anything pinned changed. Otherwise it runs the action as the user and records `job_id`.
  - Approving `deploy-feed` also marks its pinned scripts reviewed.
  - A decided proposal cannot be decided again (`proposal_decided`).

**Holdout scenarios and variants** ([ADR-0018](adr/0018-holdout-scenarios.md)).

- **Marking.** The command `scenario-holdout` (`key`, `holdout` true or false; app only) marks a scenario as holdout. `GET workspace` adds `holdout` to each scenario for the app.
- **What agents cannot do.** Agents get `403` and `"code": "holdout"` for any request that reads, runs on, or reveals a holdout scenario. Holdout runs are recognized by their data or manifest hash, so a copy stays hidden. Holdout scenarios, Feed experiments fed by them, and cell results on them are left out of what agents list.
- **Holdout tests.** An agent may propose `holdout-test` with `cells`, once per cell version (`holdout_used` after that). Approving it runs a suite on every holdout scenario, and only the user sees its results.
- **Variant counts.** Summaries returned to agents carry `variants_tested`, the number of distinct cell settings that connection has simulated, with a caveat from 20. Proposals record it too.

**Paths agents can use.** These checks come before any tier check, and a refusal is `not_permitted`:

- Agent requests cannot name a path in a hidden folder, or one containing `..`. That holds for any of `db`, `config`, `source`, `data`, `output`, `key`, `actions`, `scenarios`, or a route like `scripts/{key}`. So `.quantarium/` (grants, logs, provenance) is out of reach.
- Scenario jobs must write inside a scenarios folder, and replacing an existing scenario (`overwrite`) needs `manage`.
- Agents cannot pass `name` to `deploy-suite`, so Quantarium's timestamped names never replace a suite config.

**During the tutorial,** agent requests are refused with `not_permitted`. Finishing the tutorial copies its files, but not their provenance records.

**MCP.** `quantarium mcp` exposes these operations as MCP tools over stdio ([ADR-0015](adr/0015-agent-interface-over-mcp.md)), sending each as an agent request with `QUANTARIUM_TOKEN`. It finds the server through `.quantarium/server.json`, which `quantarium serve` writes while running. The tool catalog, with each tool's tier, server operation, and input schema, is in [`api.schema.json`](api.schema.json); a test fails if it drifts from the code.

**Budgets, pausing, and the log.**

- `agent-connection-create` accepts optional `budgets`:
  - `jobs_per_hour`, default 60;
  - `cells_per_day`, default 50; creates and duplicates count.
- A request over budget gets `429` and `"code": "budget_exceeded"`.
- The commands `agents-pause` and `agents-resume` stop and restart every connection. While paused, agent requests get `403` and `"code": "agents_paused"`.
- `GET agents/log` (optional `connection`, `limit` up to 1000) returns logged agent requests, newest first. Each entry has `time_utc`, `connection_id`, `connection_name`, `kind`, `name`, `params` (redacted), `outcome` (`ok`, `refused`, or `error`), `status`, `code`, `message`, `job_id`, and `duration_ms`.

## Reads

| Endpoint | Parameters | Returns |
| --- | --- | --- |
| `GET health` | | `{status, version, api}` |
| `GET workspace` | | `databases` (each with its Static runs, Feed and Live experiments), `configs` (`key`, `kind` static/feed/live/suite, `name`, `cells`), `scenarios`, `models` (risk card metadata or validation errors), `decisions` (decision file summaries or validation errors) |
| `GET analysis` | `db`, `kind` (static/feed/live), `id` (Static defaults to the latest completed run), `benchmark`, optional `summary=1` or `curve_offset`/`curve_limit` | Same shape as `quantarium analyze`, including `curve`; see [Summaries](#summaries) |
| `GET timeline` | `db`, `kind`, `cell`, `id`, `from`, `through` | Same shape as `quantarium timeline` |
| `GET report` | `db`, `id` | Static run report with per-cell status |
| `GET scripts/{key}` | | A script's `source`, `sha256`, and `check` (see `script-check`) |
| `GET suite-report` | `config` (suite JSON), optional `summary=1` | Cells-by-scenarios `matrix`, `by_tag`, `caveats`; see [Summaries](#summaries) |
| `GET scenario` | `key` (scenario folder) | Summary, `provenance`, and `closes` per symbol as `[date, close]` pairs |
| `GET config` | `key` | The config file's JSON |
| `GET jobs` | | Recent jobs, newest first |
| `GET jobs/{id}` | optional `wait` (seconds, at most 120) | One job with `status` (queued, running, succeeded, failed), `result` or `error`; with `wait`, returns when the job finishes or the time runs out |
| `GET decision-inboxes` | | Each forward inbox's `name`, `symbols`, `submissions`, `on_time`, `late`, first and last on-time dates, and last arrival |
| `GET deciders` | | Registered deciders with their `command`, `env`, `training_cutoff`, and `fingerprint`; browser only |
| `GET onboarding` | | First-run tutorial state (see above) |
| `GET agents` | | Agent feature setting and notice (see above) |
| `GET cells` | optional `archived=1`, `scope` (`static` or `feed`) | Cell cards: name, mode, status, problems, deployment counts, optional `model_card` and latest Lab status for this version, and the headline result of the latest (or latest scoped) deployment |
| `GET cells/{id}` | | The cell record with its versions, deployments, card, and every deployment's result |
| `GET scripts` | | Strategy classes found by parsing workspace files, plus scripts existing cells use |
| `GET holdings` | `db`, `kind`, `id`, `cell`, optional `from`, `through` | Per-holding summary, fills, and series sized for the chart, tile, or aggregate view |
| `GET arena` | | Feed Batch experiments with their cells, linked to library cells where possible |
| `GET tapes/{cell_id}` | | A cell's answer tape, newest first (up to 500 entries): day, session, status, answer or error, tokens, estimated cost. App only |
| `GET supercells` | | Every cell that uses a risk card, asks a model, or has an allocator: its parts (with the model's training cutoff and a summary of its answer tape) and the evidence of its latest Lab experiment. App only; agents are refused |
| `GET lab` | | Newest-first Lab ledger: frozen registrations, observed attempt count, status, and completed reports/verdicts |
| `GET lab/{id}` | | One Lab experiment; agent access uses the observe tier |

### Summaries

Full results can be large. `summary=1` returns a compact form meant for scripts and AI agents, with caveats stated in words.

**`GET analysis?…&summary=1`** returns:

- `kind`, `experiment_id`, `status`, and `benchmark`;
- `costs`: the slippage and commission a Static run used;
- `curve_points`: how many points the full curve has;
- `cells`: headline metrics per cell: return, drawdown, Sharpe, volatility, exposure, turnover, fills, and forward-test figures for Feed;
- `warnings` on a cell when it stopped early, has fewer than 60 sessions, has fewer than 4 filled orders, or got more than half its gain from one session;
- `caveats` for the experiment, such as no costs modeled or a scenario-fed Feed.

**`GET suite-report?…&summary=1`** returns, per cell: scenarios run, failures, mean and worst return, the worst scenario, and mean drawdown. Its caveats include the suite's data-kind caveats, plus one when the suite has a single scenario.

**Paging the curve.** Without `summary`, `curve_offset` and `curve_limit` (at most 5000) return one page of `curve`, with `curve_total` and `curve_offset`.

## Writes

`POST /api/v1/jobs` with `Content-Type: application/json`, the header `X-Quantarium-Token`, and a body `{"action": ..., "params": {...}}`. The page served at `/` carries the token in `<meta name="quantarium-token">`; `serve` also prints it for scripts. A valid request returns `202` with the queued job. Jobs run one at a time.

Add `"idempotency_key"` (1 to 128 characters) to the body to make a retry safe. Submitting the same action and params with the same key returns the first job instead of queuing another, as long as that job is still listed. Reusing a key for a different request returns `409 idempotency_conflict`.

| Action | Params |
| --- | --- |
| `run-static` | `config` |
| `run-suite` | `config`; result includes the suite report |
| `lab-run` | `id` of a registered Lab experiment; user only, runs once and returns the updated ledger record. While it runs, `GET lab/{id}` has `progress` (`stage`, `scenario`, `step`, `of`) and, for a cell that asks a model, `model_calls` |
| `cell-ask-grant` | `id`, `script` (one of the cell's scripts), `connection_id` (a model API connection), optional `mode` and budgets (`max_calls`, `max_tokens`, `max_output_tokens`, `max_estimated_cost_usd`, `input_usd_per_million`, `output_usd_per_million`, `min_sessions_between_calls`) | Grants `context.ask` to that script, pinning its current bytes; app only |
| `feed-batch` | `config`, optional `through` (YYYY-MM-DD) |
| `retire-cell` | `kind` (feed/live), `db`, `id`, `cell`, optional `reason` |
| `scenario-generate` | `spec` (object), `output`, optional `overwrite` |
| `scenario-transform` | `source`, `spec`, `output`, optional `overwrite` |
| `scenario-slice` | `source`, `output`, `from`, `through`, `name`, optional `description`, `tags`, `actions`, `overwrite` |
| `data-import` | `folder` (a `quantarium-data-v1` folder, [format](data-format.md)), `name`, optional `output` (default `scenarios/<name>`), `description`, `tags`, `overwrite`. Returns the scenario summary, its `survivorship`, and `warnings`; user only |
| `deploy-static` | `cells` (ids), `data`, optional `actions`, costs: `slippage_bps`, `commission_per_order`, `commission_per_share`, `commission_minimum` (a filled order pays at least this), `slippage_range_fraction` (0–1; adds this share of the previous session's high-to-low range, over its close, to the slippage) |
| `deploy-suite` | `cells`, `scenarios`, optional `name`, `benchmark` (a cell id), `symbol_maps`, costs |
| `deploy-feed` | `cells`, `feed_id`; for a new experiment also `provider` (`csv` or `scenario`), `source`, `start_date`, and optionally `through` to release a scenario only up to a date; costs |

Running a config executes its strategies, which are trusted local Python.

Quick changes to the cell library and the tutorial go to `POST /api/v1/commands` with the same headers and a body `{"command": ..., "params": {...}}`. They run immediately and return `200` with their result.

| Command | Params |
| --- | --- |
| `cell-create` | optional `name` |
| `lab-register` | `idea` (stable ID), `name`, `cells` (exactly one cell ID), `scenarios` (1–20 non-holdout scenario keys), optional `symbol_maps`, `pass_rule` (`min_mean_excess_return_pp`, optional `max_drawdown_pct`, `min_sessions` at least 20), costs as for `deploy-static` (only nonzero extra costs are pinned, as `costs`, and shown in the verdict's `scope`), optional `disguised_replay` (boolean); user only. Verdict rows add `lineage`, `model_contamination` (`clean`, `contaminated`, or `unknown`, for a cell that asks a model), and `ablations` (each switched-off part's return and contribution); the verdict adds `disguise` when requested ([ADR-0035](adr/0035-llm-and-hybrid-supercells.md)), and `statistics`: per scenario `sharpe_annual`, `psr`, `dsr`, `trials`, `expected_max_sharpe_annual`, and `variance_source`, plus `attempts` and `pbo` (the probability of backtest overfitting, or `null` with a `reason`). A supported verdict needs a `dsr` of at least `dsr_threshold` (0.95) in every scenario |
| `cell-save` | `id`, optional `name`, `spec`; saving changes to a version with results creates a new version. `spec.model_card` may name a JSON card in a configured models folder; its saved `information_end` must match the card. `spec.ask` grants `context.ask` to one reviewed script: `connection_id`, `model`, `workspace`, `tape` (a `.jsonl` file under `.quantarium/tapes/`), `mode` (`record` or `replay`), `approved_script_sha256`, `max_calls`, `max_tokens`, `max_output_tokens`, `max_estimated_cost_usd`, `input_usd_per_million`, `output_usd_per_million`, and `min_sessions_between_calls`; user only ([ADR-0034](adr/0034-ask-gateway-and-tape.md)). A Complex cell's `spec.allocator` is `{"builtin": true, "class": "TrendRegime" or "FixedAllocation", "parameters": {...}}`, or `{"script": key, "class": name, "parameters": {...}}` for a class with `allocate(context, sleeves)` in the sleeves' folder ([ADR-0036](adr/0036-composite-and-regime-supercells.md)). Instead of scripts, `spec.sleeves` may be `[{"decisions": key}]` (a decision file in a `decisions` folder) `[{"inbox": name}]` (a forward inbox), or `[{"decider": name}]` (a registered decider; user only) ([plan](plans/v0.6.x-referee.md)) |
| `decision-inbox-create` | `name` (1–64 letters, digits, dashes, underscores), `symbols` (the symbols its decisions may name) |
| `decision-submit` | `inbox`, `date` (the session whose close it decides for), `weights` (symbols to weights from 0 to 1, or `null` to hold), optional `orders` and `note`. Returns the record with `received_utc`, `deadline_utc` (the next session's 09:30 New York open), and `on_time`. Only on-time decisions count; a later on-time one for the same date replaces the earlier |
| `decider-create` | `name`, `command` (the program and its arguments), optional `env` (names of environment variables it may receive) and `training_cutoff`; user only |
| `data-check` | `folder`: validates a `quantarium-data-v1` folder and returns its summary and warnings; user only |
| `trades-convert` | `trades` (a CSV with `date,symbol,side,shares,price`), optional `output` (default `decisions/<name>.json`), `prices` (a price file whose sessions to use instead of the NYSE calendar), `system`, `overwrite`. Returns the report with suggested `initial_positions`, `warnings`, and `limits`; user only |
| `decider-delete` | `name`; user only |
| `agent-connection-daily-limit` | `id` (a model API connection), `usd` (dollars per UTC day across all its cells' tapes, or `null` to remove the limit); user only |
| `cell-duplicate`, `cell-archive`, `cell-restore` | `ids` |
| `cell-delete` | `ids`; refused for cells with deployments |
| `onboarding-start`, `onboarding-step`, `onboarding-finish` | see [First-run tutorial](#first-run-tutorial) |
| `agents-enable`, `agents-disable` | see [Agent features](#agent-features) |
| `script-write` | `key` (a `.py` path inside a scripts folder, not in a hidden folder), `source`, and `base_sha256` when the file exists (the `sha256` it was read with); returns `key`, `sha256`, and `check`. A stale `base_sha256` fails with `script_changed` |
| `script-check` | `key` or `source`; parses without running and returns `ok`, the strategy `classes`, `problems`, and `sandbox_problems` |
