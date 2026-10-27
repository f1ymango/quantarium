# ADR-0034: The `ask` gateway and the answer tape

- **Status:** Accepted for v0.4.4
- **Date:** 2026-10-23
- **Deciders:** Barloom maintainers
- **Draft:** ChatGPT (commit `880ab98`); reviewed and completed by Claude

ADR-0033 is used by an unmerged collaborator branch, so this record is 0034.

## Context

Supercells may let a language model inform a strategy's decisions ([plan](../plans/v0.4.x-lab-and-supercells.md), section 3.7). Four risks come with that:

- a script with network access and keys would break the sandbox;
- model answers vary between calls, which breaks Barloom's "same input, same result";
- a backtest can make thousands of paid calls;
- a model may remember the history it is tested on.

## Decision

**One sanctioned path.** A strategy calls `context.ask(prompt, schema)`. Barloom makes the call through one of the user's model API connections (ADR-0021), with no tools, and returns only an answer that matches the schema. In the sandbox, the request crosses the existing pipe to the parent process, so sandboxed agent code still has no network access or keys. Reviewed user scripts run as ordinary local Python, so the gateway is their sanctioned path, not a containment boundary.

**A grant per cell, from the user only.** A cell's `spec.ask` names:

- the connection and the pinned model;
- the tape file under `.barloom/tapes/`;
- `record` or `replay` mode;
- the SHA-256 of the one script allowed to ask;
- budgets: calls, tokens, estimated cost, and output tokens per call;
- a decision rhythm: the minimum number of sessions between calls.

These rules apply to the grant:

- Agents can neither add, change, nor copy a grant.
- A script an agent wrote must be reviewed before it may be granted.
- Editing the script voids the grant until it is granted again.
- Agent features must be on, and not paused, for record mode.
- Live Feed refuses cells with a grant.

**Schemas and answers.**

- Schemas are limited to JSON types within three levels, with bounded strings, arrays, and properties, and no unknown keywords.
- Prompts are at most 8,000 characters.
- The answer must be a single JSON text. Thinking blocks are skipped, and one surrounding code fence is removed.
- An answer outside the schema, its enums, or its bounds is refused.
- The model is told to treat market text as data.

**The tape.**

- Each new answer is appended to the cell's JSONL tape, with the prompt and schema hashes, the model, tokens, and estimated cost, and is written to disk before use.
- The same prompt and schema are answered from the tape at no cost. Replay mode never calls a model and refuses prompts missing from the tape.
- Each answer is also a `model_answer` journal event, marked `recorded` or `tape`.

**Budgets cover the tape's whole life**, so rerunning a backtest never refills them. The rhythm counts sessions within one run, because session numbers restart with each run.

**The Lab.** A model's training cutoff cannot be verified, so a candidate with a grant earns a verdict only when every scenario is synthetic. On historical or transformed scenarios the verdict is `unknown`, with a caveat that the model may remember what happened. Contamination labels from disclosed cutoffs, and the disguised replay, are v0.4.5.

## Fixes made to the draft

| Problem | Fix |
| --- | --- |
| The rhythm was loaded from the tape, so a second run refused every new question | The rhythm counts within the run |
| Replay mode reported a missing answer as an exhausted budget | Replay checks the tape first |
| The adapters sent `tools: []`, which OpenAI's API refuses | No tool list is sent when there are no tools |
| A thinking block or a fenced answer failed the one-block check | Thinking is skipped and one fence is removed |
| An unreviewed agent script could be granted | Review is required first |
| Agents could copy a cell with a grant | Refused |
| A Lab verdict on real history ignored that the model may remember it | `unknown` unless every scenario is synthetic |

## Consequences

- A backtest's model answers are reproducible and auditable from its tape, and replays cost nothing.
- Answers on the tape are only as trustworthy as the model and prompt that produced them. Barloom checks their shape, not their sense.
- Estimated cost uses the per-million prices typed into the grant; Barloom does not know providers' prices.
- Verified with a local model: qwen3:4b through Ollama answered a strategy's prompt within its schema, and the replay of the same backtest used the tape without a call.
