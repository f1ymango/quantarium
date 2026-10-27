# Security review: v0.5

- **Date:** 2026-10-24
- **Status:** Complete. Released in v0.5.5.
- **Scope:** Barloom's own code as of v0.4.10, and everything added since the v0.3.0 review (`docs/security-review-v0.3.0.md`): model connections and agent app runtimes (v0.3.1 to v0.3.4), the Lab, supercells, the `ask` gateway and tapes, allocators, risk cards, and `serve --fresh` (v0.4). Kandodaki's ML branch (PR #2) is out of scope until it is reviewed and merged.
- **Method:** misuse tests, as set out in `docs/plans/v0.5.x-security-hardening.md`. Each test uses Barloom the way a careless or hostile agent, model, agent app, or file might, against a disposable workspace on loopback, and checks that Barloom refuses safely. Every finding below has a regression test in `tests/security/` that fails without its fix.

## Rules the tests follow

- They target Barloom's code, on loopback, in temporary workspaces.
- They plant fake canary secrets: model, broker, and GitHub keys in the environment, and SSH, cloud, agent-app, and browser files in a fake home folder. Every test ends by checking that no canary reached a response or any workspace file.
- A network guard refuses every connection and name lookup in the test process that is not loopback, and fails the test if one was tried. Keys go to an in-memory stand-in for the system keychain.
- A folder guard fails the test if anything outside its workspace changed: the fake home, a sentinel folder, or Barloom's own source.
- Model APIs and agent apps are fakes. No test contacts a real service, uses a real account, or starts a real agent app.
- Sandbox tests check Barloom's own refusals. They do not look for ways around Python's or the operating system's protections.

## Threat model

The five attackers of the v0.3.0 review still apply: a website in the user's browser, a manipulated or mistaken agent, untrusted strategy code an agent wrote, leaks of secrets, and path escapes. v0.5 adds four:

6. **A misbehaving or hostile model API** behind a connection the user created: crafted answers, odd usage reports, redirects.
7. **A misbehaving agent app** (Claude Code, Codex, OpenCode, or a custom ACP agent): unannounced or disguised tools, floods, malformed messages.
8. **Damaged or hostile files** in the workspace: price files, scenario manifests, configs, cells, risk cards, tapes, and Barloom's own settings, as a bad download, a careless edit, or a broken disk might leave them.
9. **Self-deception in the Lab:** a verdict that looks better than the evidence, through reused data or a stale registration.

Out of scope, as before: other software running as the same user with shell or file access (ADR-0014). Someone who can edit files in `.barloom` is the user, or has the user's access.

## Findings and fixes

| # | Severity | Version | Finding | Fix |
| --- | --- | --- | --- | --- |
| G1 | High | v0.5.2 | Provenance recorded an agent-written script under the path as the agent spelled it, and checked it under the canonical path. A script written as `strategies/agent/./x.py`, `strategies/agent//x.py`, or with other capitals on Windows was treated as the user's own and ran in Barloom's process, outside the sandbox. The default tiers (draft and simulate) were enough. | Writes record the canonical key. `provenance.same_key` normalizes separators, `.` and `..`, and case on Windows, so older records match too. |
| G2 | High | v0.5.2 | The loader checked only the script it was asked to run. A reviewed script that imported an unreviewed agent-written module ran that module in-process. | An import guard on `sys.meta_path` (`provenance._ImportGuard`) refuses unreviewed agent-written files on every import in the process. |
| G3 | Medium | v0.5.2 | Loading a strategy left its folder first on the server's `sys.path`, so a later import anywhere in the server could resolve to an agent's file of the same name. | `engine._importable` puts the folder on the path only while the strategy loads. |
| G4 | Medium | v0.5.4 | Agent apps received every environment variable except a short list of model and broker key names. `GITHUB_TOKEN`, `AWS_SECRET_ACCESS_KEY`, and similar secrets reached them. | Variables whose names contain `SECRET`, `TOKEN`, `PASSWORD`, `CREDENTIAL`, `API_KEY`, `ACCESS_KEY`, or `PRIVATE_KEY`, end in `_KEY`, `_SECRET`, or `_PAT`, or start with a known provider's prefix are removed. |
| G5 | Medium | v0.5.4 | A tool call counted as one of Barloom's when its title merely contained a Barloom tool's name. An agent app that reported a shell command mentioning one, without marking it as a command, got permission for it. | The whole title must name the tool, with at most a plain note in parentheses. |
| G6 | Medium | v0.5.4 | The `ask` gateway read usage before counting a call. A model API that reported usage as text or `NaN` could be called again and again without using up the call budget. | The call is always counted. Output that is not reported plainly counts as the most the model may write. |
| G7 | Medium | v0.5.3 | The Lab recognized reused data by scenario family, which for a generated or imported scenario is its manifest hash. The same prices under another name, or in a copied folder, counted as fresh data, so an idea could earn a verdict on data it had already used. | Reuse also matches the exact prices. Scenarios that share prices within one experiment get the "not independent evidence" caveat. |
| G8 | Medium | v0.5.1 | The server set no socket timeout. A client that stopped in the middle of a request held a server thread indefinitely. | `Handler.timeout` is 30 seconds. |
| G9 | Low | v0.5.1 | Deeply nested JSON exhausted the recursion limit and returned a 500. | Bodies nested more than 64 levels are refused with a 400. |
| G10 | Low | v0.5.1 | Request bodies could contain `NaN`, `Infinity`, and numbers too large to be finite. `NaN` passes every bound check unnoticed. | Refused when the body is parsed. |
| G11 | Low | v0.5.1 | A cell name that was a number or an object, or a `spec` that was not an object, crashed `cell-create` and `cell-save` with a 500. Both failed closed. | Names must be text of at most 200 characters without control characters; `spec` must be an object. |
| G12 | Low | v0.5.1 | Trading costs for Static and suite deployments were checked only inside the job, after a config was written. | Checked when the job is submitted. |
| G13 | Low | v0.5.1 | A malformed MCP message ended the stdio session. | Type checks in `tools/call`; any failure is answered with a JSON-RPC error. |
| G14 | Low | v0.5.3 | A `NaN`, negative, or non-numeric amount in a tape could switch the cost budget off or break the Supercells view. Tapes live in `.barloom`, out of agents' reach. | The gateway refuses such a tape; the summary skips damaged entries. |
| G15 | Low | v0.5.3 | Short CSV rows, infinite values in vendor files, and wrongly typed config and suite entries raised `AttributeError` or `OverflowError`, which the API reports as a 500. | Ordinary `ValueError`s, reported as a 400. |
| G16 | Low | v0.5.4 | Plain `http://` endpoints accepted any `*.localhost` name. Such names may be resolved by a DNS server, and a key sent over plain HTTP would follow the answer. | Only `localhost` and loopback addresses. |
| G17 | Low | v0.5.4 | An agent app's flood of text was kept in full, and malformed updates or requests ended the turn with an exception. | A turn stops after 200,000 characters or 200 tool calls; wrongly shaped messages are ignored or answered with an error. |
| G18 | Low | v0.5.2 | `script-write` applied path rules to source code, refusing scripts whose text contained `../` or a dotted folder name. A usability fault, not a weakness. | `source` is exempt for `script-write` and `script-check`. |

## What was checked and held

- **Browser boundary.** Foreign `Host` headers, foreign and `null` origins, and the wrong content type are refused. Malformed bodies (deep nesting, wrong types, bad encodings, very many keys) get a 4xx.
- **The agent checkpoint.** Seeded fuzzing of every agent operation and prefix route, with hostile values in every known parameter, produced no 5xx, no canary, and no change outside the workspace. An observer-only connection changed nothing by any route spelling. Browser-only routes stay closed to agents. Wrong, altered, and revoked tokens are refused.
- **Paths and holdout.** Every path parameter refuses hidden and outside paths. Fourteen spellings of a holdout scenario's path (case, separators, trailing dots, `..`, absolute paths, files inside the scenario) are refused across reads, jobs, and Lab proposals, and the holdout never reached an agent. The Lab also compares manifests, so it refuses a holdout under any path.
- **Code execution.** The sandbox refuses canary file reads, `os` and `socket` imports, and dunder access. Agents cannot write `.pth`, `.pyc`, `.pyw`, package, `sitecustomize`, hidden, outside, alternate-stream, short-name, or device-name files. The sandbox child drops `ctypes` from its module cache before its audit hook goes on, so the hook refuses it even when numpy loaded it (adapted from Kandodaki's PR #2). Model card paths follow the path rules. Agent-written allocators load through the same checks as strategies.
- **Files.** Scenario manifests and risk cards are protected by their hashes and validated field by field. Damaged cells, scenarios, configs, scripts, and databases never made a read fail with a 5xx. Damaged agent settings fail closed, with agents refused. Unreadable provenance stops agent scripts from running as the user's own code.
- **Models.** Endpoints with user names, fragments, queries, odd schemes, embedded whitespace, or a loopback name inside another host are refused. Crafted answers never pass the schema: out of range, wrong type, `NaN`, extra fields, Markdown fences, or arrays. Redirects are never followed with a key (`tests/test_providers.py`, with the `[assistant]` extra).
- **Agent apps.** File and terminal requests are refused, and permission requests of the wrong shape grant nothing. Agent apps start in an empty folder, with no canary in their environment.
- **Pages.** No raw-HTML or code-from-text sinks outside vendored code, and every link built from data is an in-app route (`tests/security/test_static_patterns.py`).
- **Risky code.** `exec`, `eval`, dynamic imports, `pickle`, `marshal`, shell commands, and YAML loading appear only at the reviewed places listed in `test_static_patterns.py`: the trusted strategy loader and the sandbox child.

## Accepted risks

All the accepted risks of the v0.3.0 review still apply. In addition:

- **Name-based secret filtering.** Agent apps' environments are filtered by variable name. A secret under a name that matches none of the patterns, such as `MYSERVICE_AUTH`, still reaches them. An allowlist would be stricter, but agent apps need much of the user's environment to find their own sign-ins.
- **Agent apps' own reports.** Barloom judges an agent app's tool calls by the kind and title the app reports. An app that misreports a shell command as an ordinary tool with a Barloom tool's exact name could still get permission. The app is software the user installed; its own permission system is the boundary for its own tools.
- **`sitecustomize` in the scripts folder.** With `edit_user_scripts`, an agent may write `strategies/sitecustomize.py`. Python reads `sitecustomize` only at start-up, from its own path, and the file is unreviewed, so the import guard refuses it.
- **Bounded fuzzing.** The default run uses fixed seeds and a few rounds per operation. A deeper run (seed 7, five times the rounds) found nothing further; `BARLOOM_FUZZ_SEED` and `BARLOOM_FUZZ_ROUNDS` explore more.

## What remains untested

- **Real agent apps.** Every agent app test uses fakes. Codex, OpenCode, and other ACP agents were not run against the new limits. Claude Code stays experimental and is never tested on the maintainer's account.
- **Live and Alpaca paths.** They need the network, which these tests never reach.
- **Rendering in a browser.** The pages are checked by static rules, not by rendering hostile text in a browser.
- **Dependency scanning.** `bandit` and `pip-audit` were not installed (maintainer's decision, 2026-10-24).
- **A second reviewer.** No independent review of this scope was made (maintainer's decision, 2026-10-24).
- **PR #2.** Kandodaki's ML branch was closed unmerged on 2026-10-24 and kept as research reference (`ml-review`, `ml-signals`). Only its `ctypes` cleanup was ported. Before other pieces come across, its workflow and `[ml]` dependencies must meet `AGENTS.md`, its manifest reader needs a path-containment check, and its watch list must leave tracked files.
- **Numpy in the sandbox.** Neither test environment has numpy, so the `ctypes` cleanup is tested as a unit and by its place before the hook, not with numpy loaded.
