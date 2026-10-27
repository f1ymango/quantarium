# Versioning

Quantarium follows [semantic versioning](https://semver.org): `MAJOR.MINOR.PATCH`. This page says what that means for a strategy lab whose results people rely on. Agreed with the maintainer on 2026-10-27.

## One version, everywhere

- The version is written in `pyproject.toml`, `quantarium/__init__.py`, the README's first lines, and the newest heading of `CHANGELOG.md` (`## vX.Y.Z — date`). They always agree; `tests/test_version.py` fails when they do not.
- A released version has exactly one commit, one annotated git tag `vX.Y.Z`, and one changelog entry. Tags are never moved, deleted, or reused, and neither are PyPI versions.
- Pre-release labels (`rc`, `beta`) are not used. Versions only go up.

## What the numbers mean

**MAJOR** when something users rely on stops working:

- the strategy contract: `on_bar`, `Context`, `Decision`, `Order`, and what a strategy returns;
- config keys, cell specs, and the workspace layout;
- the `/api/v1` API, MCP tool names and parameters, and CLI commands and options;
- a file format that old files can no longer be read in (data folders, scenarios, decision files, inboxes, tapes, evidence reports).

**MINOR** for new features, new commands, new optional settings, and new file-format versions, while everything from the same major version keeps working.

**PATCH** for fixes, wording, documentation, and security fixes that change no interface.

## Results are part of the interface

Quantarium judges strategies, so a change in its numbers matters as much as a change in its API.

- If the same inputs would give different fills, equity, statistics, or verdicts than before, the changelog says so under a **Results change** heading, naming what moved and why.
- A deliberate change to how results are computed is at least a **MINOR** release. A bug fix that changes results may be a PATCH, but is flagged the same way.
- Runs, Lab records, and evidence reports record the version that made them, so an old verdict can always be read against the engine that produced it. The backtrader cross-check must still pass before any release.

## File formats

File formats carry their own version in their names (`quantarium-data-v1`, `quantarium-decisions-v1`, ...). Within a major version, readers keep accepting every format version they ever wrote, and file-format names used before 1.0 stay readable throughout 1.x.

## Deprecation

Something to be removed is announced in a MINOR release, with its replacement, and removed no earlier than the next MAJOR. The names used before 1.0 stay supported throughout 1.x.

## Security fixes

Security fixes ship as soon as they are ready, as a PATCH on the latest release, with an advisory and a changelog entry. Only the latest release is supported ([SECURITY.md](../SECURITY.md)).

## Development steps and releases

- Each step of development bumps the version in its commit, as the changelog shows.
- **Before the public release**, every step is also tagged when the maintainer ships it.
- **After the public release**, only the versions the maintainer chooses to release get a tag and a PyPI upload; a release may contain several steps.
- The public repository starts from one clean commit carrying whatever 1.0.x version is current when it is published. Its changelog keeps the earlier entries, so the private history is explained rather than hidden.

## The transition to 1.0.x

The first four steps of 1.0 were committed as `1.0.0rc1` to `1.0.0rc4` and later tagged `v1.0.1` to `v1.0.4`, so those four tagged commits still say `rc` in their files. The commit after `v1.0.4` set the version to `1.0.4` everywhere and added this page; the rules above apply in full from `v1.0.5` on.
