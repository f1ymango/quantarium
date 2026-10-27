# ADR-0005: Load local strategy entrypoints by source content

- **Status:** Accepted for local single-file entrypoints
- **Date:** 2026-10-20
- **Deciders:** Barloom maintainers; implementation choice recorded for user review

## Context

Barloom pins the SHA-256 of each strategy entrypoint file in saved experiments. Python's ordinary `import_module` cache is keyed by module name, not by the config folder or file content. Running two experiments in one process with `choice:Pick` from different folders can reuse the first folder's class. Editing a script in the same process can likewise execute old code while the file hash reflects new bytes. That breaks the meaning of saved source provenance.

## Decision

For a local single-file `module:Class` entrypoint found in the config folder, read the file bytes once, compute their SHA-256, and compile those same bytes under an internal module name derived from the absolute path and digest. Reuse the loaded module only when both path and digest match. An edit therefore creates a new loaded version, while previously instantiated strategy objects keep their original behavior. Continue ordinary Python import behavior for package or installed-module entrypoints.

## Alternatives considered

| Choice | Benefit | Cost or reason not chosen |
| --- | --- | --- |
| Keep `import_module` caching | Simple and fast | Can execute a different strategy from the bytes recorded in the experiment |
| Call `importlib.reload` for every cell | Picks up edits | Mutates a shared module object and can affect existing strategy instances or other experiments |
| Start a new OS process for each cell | Strong module isolation | Duplicates interpreter and feed state, increasing laptop resource use |
| Content-address local entrypoint files | Aligns executed bytes with the saved hash | Uses an internal module name, and imported helper modules still follow Python's normal cache |

## Consequences and follow-up

Local entrypoint scripts are loaded consistently across repeated API calls in one process. Their digest covers the entrypoint file, not every transitive import. For reproducible multi-file strategies, use a fresh process and preserve the helper files, or extend the provenance model to hash and isolate the entire local module dependency graph. Strategy scripts remain trusted local code; this decision does not sandbox them.
