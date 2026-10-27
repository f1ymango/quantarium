# Security review: v1.0

- **Date:** 2026-10-26
- **Status:** Complete. Released in v1.0.4 (committed as 1.0.0rc4; see [versioning](versioning.md)).
- **Scope:** everything added since the v0.5 review (`docs/security-review-v0.5.md`), now that Quantarium is about to become public: the referee (v0.6: decision files, forward inboxes, deciders), evidence (v0.7: trial statistics, evidence reports, the price table, daily cost limits), the engine (v0.8: the backtrader cross-check, cost models, savepoint journals, the faster loader), the user's own data (v0.9: the data format, changing universes, survivorship caveats, trades files, the Futu bridge, the app), and 1.0 (the rename from Barloom with its compatibility layer, the license documents, and packaging).
- **Method:** as for v0.5. Each surface was read against the invariants in `AGENTS.md` section 5, misused the way a hostile agent, file, or program would, and every fix ships with a test that fails without it. The negative check (the test failing against the old code) was run for each fix below that has one.

## Rules the tests follow

Unchanged from v0.5: loopback only, temporary workspaces, fake canary secrets, a network guard, a folder guard over everything outside the workspace and over Quantarium's own source, fake model APIs and agent apps, and no attempt to get around Python's or the operating system's protections. No test used a real account, broker, model API, Futu OpenD, or PyPI.

## Threat model

The nine attackers of the v0.5 review still apply. Going public adds three:

10. **Strangers' files.** Data folders, trades files, decision files, and evidence reports will now come from people the user does not know, and may be crafted.
11. **Shared reports.** Evidence reports are meant to be passed around, so anything they reveal about the computer that made them reaches strangers.
12. **The package itself.** A published wheel must contain only what was reviewed, built with tools installed under the rules of `AGENTS.md` section 2.

Out of scope, as before: other software running as the same user with shell or file access (ADR-0014). Deciders are the user's own programs: they run with the user's rights and the variables the user names for them, and are bounded but not sandboxed.

## Findings and fixes

| # | Severity | Version | Finding | Fix |
| --- | --- | --- | --- | --- |
| 1 | High | v1.0.1 | Agents may not start the user's deciders. For a config an agent asked to run, the check searched the config's **text** for the decider entrypoint, so a JSON escape (`\u004civeDecider`) or, after the rename, the old package name (`barloom.referee:LiveDecider`, which the alias package resolves) passed it and would have run the user's program on the agent's behalf. | The check decodes the JSON and refuses any value naming the decider class under any package name (`tests/security/test_decider_misuse.py`). |
| 2 | Medium | v1.0.4 | Evidence reports embedded the Lab record as stored, and records of cells using decision files, inboxes, deciders, or model access hold absolute paths, such as `C:\Users\<name>\...`. A shared report showed strangers the user's folders and account name. | Reports replace the workspace's path with `<workspace>` and the home folder with `~`; `verify-report` applies the same rule to the ledger before comparing, and still accepts earlier reports (`tests/test_evidence.py`). |
| 3 | Low | v1.0.4 | A decider's replies were read a whole line at a time before their size was checked, so a reply that never ended its line could fill memory (a 40-million-character line reached an 80 MB peak). | Replies are read in bounded pieces and an oversized one is refused without being held (`tests/test_deciders.py`, peak under 20 MB). |
| 4 | Low | v1.0.1 | While rewriting format checks for the rename, a `format` field holding a list or object raised `TypeError` instead of being refused. The data-folder fuzz test found it before release. | `is_format()` accepts strings only (`tests/security/test_user_data.py`). |
| 5 | Low | v1.0.4 | The compatibility layer renamed any strategy parameter that began with `barloom.`, so a parameter such as `barloom.csv` would have silently changed. | Only whole `barloom.module:Class` entrypoints are renamed (`tests/test_legacy.py`). |
| 6 | Low | v1.0.4 | The app's **Make a decision file** replaced an existing decision file, which a Lab registration may have pinned, without asking. | The API refuses with `409 already_exists` unless overwriting is asked for, and the app no longer asks (`tests/security/test_outside_data.py`). |
| 7 | Integrity | v0.9.1 | In a changing universe, the run's calendar first came from the cell's own symbols, so a cell whose stocks all delisted ended before their payout and kept a bankrupt stock at its last price: a flattered result. | The calendar comes from the whole price file (`tests/test_universe.py`). |
| 8 | Packaging | v1.0.3 | An ignored `build/` folder from an old Barloom build put 63 stale files into the first wheel. | Removed; the release steps start by removing it (`docs/releasing.md`), and the wheel was inspected file by file. |

## What was checked and held

- **Agent boundaries.** Agents cannot register, choose, or run deciders; cannot import research data, check data folders, or convert trades files (none of these is in the operation table, and each also refuses agent tokens itself); cannot reach hidden folders under either name, since any path part starting with a dot is refused before it is resolved. They may read the scenarios the user imported.
- **Untrusted files.** Data folders, trades files, decision files, decider tapes, forward inboxes, scenario manifests, and evidence reports are bounded in size and count, parsed strictly (no duplicate keys, `NaN`, or infinities), and fuzzed with hostile strings in every field. Reading never runs code.
- **Forward decisions.** Inboxes are append-only; a decision counts only if Quantarium stamped it before the next session opened; finished decision files and trades files earn at most an `unknown` verdict.
- **Evidence.** Reports load nothing from the internet, render every value as text, and verify their embedded record against its hash and, given the workspace, against the ledger and pinned files.
- **Secrets.** Agent apps are started without any variable under `QUANTARIUM_` or `BARLOOM_` (or other secret prefixes); both token prefixes, `qtm_` and `blm_`, are treated as secrets in logs; keys stay in the keychain, now read under either service name and never returned. The Futu bridge needs no keys, talks only to loopback, and its source names no trading API.
- **The rename.** Tokens are checked by hash whatever their prefix; the alias package only maps `barloom.X` to the already-guarded `quantarium.X`, never to a workspace file; saved records are read in their current form but never rewritten; the old hidden folder is renamed once and never merged.
- **Engine changes.** Costs are validated as finite and bounded; a failing day still rolls back exactly under savepoint journals; the changing-universe path replays fixed universes event for event as the classic path, which the backtrader cross-check covers (68 of 68 cases).
- **Packaging.** The wheel holds 111 `quantarium` files, the 2-file alias, and the license files; the source archive leaves out tests; setuptools was pinned to a release over seven days old and its one `.pth` file was read.

## Accepted risks

- **Declarations are the user's word.** A data folder's survivorship declaration, a decider's training cutoff, and a cell's information boundary are recorded and shown, never verified.
- **Agents can read imported data.** Scenarios are readable at the observe tier, including ones built from research data the user bought; a very large scenario is slow to read.
- **Deciders run as the user.** They are bounded (time, reply size, named variables, an empty folder) but not sandboxed, since they are the user's own programs.
- **Old names stay accepted** for as long as old workspaces exist: `BARLOOM_*` variables, `blm_` tokens, and `barloom-*-v1` files.
- **The Futu bridge** is tested only with fake candles.

## What remains untested

- Real agent apps (Codex, OpenCode, Claude Code) against the 1.0 build, and `quantarium mcp` from real clients: the maintainer's checks.
- Real Alpaca and Futu OpenD sessions.
- Uploading to PyPI, and GitHub's private vulnerability reporting in the new public repository, which the maintainer turns on.
