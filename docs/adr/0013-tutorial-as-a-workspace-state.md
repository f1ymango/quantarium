# ADR-0013: The tutorial as a workspace state

- **Status:** Accepted
- **Date:** 2026-10-21
- **Deciders:** Quantarium maintainers; implementation choice recorded for user review
- **Amends:** [ADR-0012](0012-first-run-tutorial-in-the-real-workspace.md)

The hidden folder has been called `.quantarium` since v1.0.1; workspaces from before then remain readable.

## Context

ADR-0012 ran the tutorial directly in the user's workspace. That works on a first launch, when the workspace is empty, but a replay in a workspace that already has content went wrong in four confirmed ways:

1. **Feed name reuse.** Name checks looked only at generated Feed configs. When a config had been deleted but its database `runs/feed-first-forward.sqlite` kept, a replay reused `first_forward` and the deploy failed: "Feed configuration changed outside its cells".
2. **Edited starter script.** If the user had edited `strategies/starter.py` and removed TrendFollower, the tutorial cell could not become ready and the tour stalled.
3. **Custom scan folders.** A `quantarium-workspace.json` that scans other scenario folders hid the generated `scenarios/first_market`, and the tour stalled.
4. **Leftovers on skip.** Skipping partway left the unnamed cell and the scenario behind.

The user's own cells and records also crowded the tutorial's pages. The user asked that the tutorial begin from "the same old state of nothing", create one new cell, and be treated as a state.

## Decision

**While the tutorial is active, the API serves a private workspace.**

- `onboarding-start` creates `.quantarium/tutorial/`, an empty workspace with default scan folders and its own copy of the starter strategies.
- While `.quantarium/onboarding.json` says `active`, every read, command, and job except the `onboarding` ones goes to an `Api` for that folder. It is created lazily, so a server restart resumes the run.
- The frontend needs no filtering. Pages remount when the state changes, and a **Tutorial** badge marks the state.

**Finishing promotes; skipping discards.**

- `onboarding-finish completed` waits for the tutorial's jobs, then promotes its cell, market, configs, and Static/Feed results into the real workspace at the same relative paths. The tutorial Lab ledger (`.quantarium/lab.json`) and database (`runs/lab.sqlite`) are discarded with the private workspace.
- Because Feed configs refer to scripts, scenarios, and databases by relative path and pin their hashes, the promoted experiment keeps working: Advance continues it.
- A file that already exists with different content stops the finish instead of being overwritten. An identical file is skipped.
- `skipped` deletes the private workspace. The real workspace is left as it was, apart from seeding `strategies/starter.py` if absent.

**Names are chosen against the real workspace, so promotion cannot collide.** At start, each of these gets the first free name:

| Name | First choice | Checked against |
| --- | --- | --- |
| Cell | `My first cell` | Cell names |
| Scenario | `first_market` | Scanned scenarios and `scenarios/<name>` |
| Feed experiment | `first_forward` | Experiment ids in every database, generated configs, and `runs/feed-<slug>.sqlite` |
| Results database | `runs/tutorial.sqlite` | Existing files |
| Script | `strategies/starter.py` | Existing files; free when the existing file is the unedited starter |

The suite and static results go to the results database; the tutorial Lab database is deliberately not promoted because its ledger remains private. Numbered names are used when the first choice is taken. The tutorial reads the script path from the run's `names`, so an edited starter is never touched.

**Upgrading.** A state left `active` by v0.2.1 has no private folder. It reads as `pending`, so the title screen offers a fresh start; content that run already created stays as ordinary workspace content.

## Alternatives considered

| Choice | Benefit | Cost or reason not chosen |
| --- | --- | --- |
| Keep the real workspace and filter views by a tutorial tag | One workspace | Every endpoint and page needs filtering; Log, Activity, and the database scans would leak; leftovers still need cleanup |
| Track tutorial-owned artifacts and delete them on skip | Content is in place immediately | Shared databases (`runs/suites.sqlite`) cannot have a run removed safely; deleting from the user's workspace is riskier than never writing to it |
| Replace the previous tutorial cell on replay | Exactly one tutorial cell ever | Deletes results the user may value; a completed replay is the user's choice to make another |
| Copy only the cell on finish and drop its results | Simpler promotion | The finale and Home would show a cell without the results the user just made |

## Consequences

The real workspace is written only when a tutorial finishes, and then only with new files. Jobs the tutorial ran appear in Activity only while it runs. Static and Feed results stay in Log after promotion; Lab practice is discarded so it cannot be mistaken for lasting evidence in the user's workspace. A completed replay adds a second cell (`My first cell 2`) with its own market and experiment. A promoted scenario lives at `scenarios/<name>`, so a workspace whose `quantarium-workspace.json` scans only other scenario folders won't list it, although the cell's results still refer to it. The tutorial folder lies under `.quantarium/`, which workspace scans skip.
