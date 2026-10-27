# ADR-0012: First-run tutorial in the real workspace

- **Status:** Accepted; amended by [ADR-0013](0013-tutorial-as-a-workspace-state.md)
- **Date:** 2026-10-21
- **Deciders:** Barloom maintainers; implementation choice recorded for user review
- **Replaces:** the v0.2.0 disposable practice workspace (`barloom/tutorial.py`)

## Context

v0.2.0 ran its guided tour in a disposable practice workspace under `.barloom/tutorial-sessions/<id>/`. The workspace was pre-filled with prepared cells and results; the user inspected them, and finishing deleted everything. The user wanted something different:

- The tutorial starts on the first launch, with nothing in Barloom yet.
- The user does the work: creates a scenario, deploys a cell in Static, exports it to Feed, advances it with Feed Batch (Live Feed is introduced, not run), and reviews Log and Activity.
- The tutorial ends on Home with one tutorial cell, which every user who doesn't skip it keeps.
- It should feel immersive, like a game.

The practice-session code also contained a Windows bug (fixed separately): its liveness probe `os.kill(pid, 0)` sends Ctrl+C on Windows.

## Decision

**The tutorial runs in the user's own workspace and creates real content:**

- a scenario (`first_market`)
- a cell (`My first cell`)
- that cell's scenario suite
- a scenario-backed Feed experiment (`first_forward`)

Replays pick numbered names that don't collide with earlier runs (`first_market_2`, …).

**First launch is detected per workspace.** `.barloom/onboarding.json` holds `status` (`pending` until started, then `active`, `completed`, or `skipped`), the current step, the tutorial cell's id, and the names for this run. While the status is `pending`, the app shows the tutorial's title screen. Progress is saved on every step, so a reload or another browser resumes there. Commands `onboarding-start`, `onboarding-step`, and `onboarding-finish` manage it.

**A new workspace starts empty.** Default scan folders no longer include the repository's `examples/` (configs, scenarios, data). Starter strategies ship in the package and are copied to `strategies/starter.py`, never over an existing file, when the tutorial starts or is skipped. Scripts already used by library cells stay in the script picker even outside the scanned folders, so existing cells don't lose theirs.

**Game structure.**

- A title screen leads into five chapters: Build a market, Hatch a cell, Train, Enter the arena, The record.
- Each chapter opens with a card listing its objectives.
- Steps are either explanations ("talk") or objectives ("do"). A spotlight dims everything but one control and blocks clicks elsewhere.
- An objective completes when the app's real state shows it happened, by polling the API or the page. It is never a "Next" button.
- A completed objective pulses green and advances. **Do it for me** completes the current objective through the same API and controls.
- A progress bar marks chapters, and a finale presents the user's new cell card.
- Steps inside a dialog name the step that opens it, so a reload that closes the dialog returns there.
- During the tour, dialogs dock at the top so the dialogue never covers them.

**Forms are prefilled while the tutorial runs.** The Generate dialog receives a tuned market: seed 21; about +27% over 90 sessions, −26% in 15, then a full recovery. That shape lets a trend follower visibly react. The Run dialog preselects that scenario, or a practice Feed experiment that releases sessions only through 2030-06-28 so Advance has work to do. "Release sessions through" is now a general option for scenario-backed Feed deployments.

The handbook and the **?** buttons are kept and rewritten for the new flow. Live Feed stays CLI-only; the Feed page gains a short Live Feed briefing panel.

## Alternatives considered

| Choice | Benefit | Cost or reason not chosen |
| --- | --- | --- |
| Keep the disposable practice workspace | Leaves no trace | Contradicts ending with the user's own cell; the user explores prepared results instead of building them |
| Run the tour in practice, then copy the cell into the real workspace | Isolation during the tour | Two workspaces to reconcile; the copied cell's results would point into deleted journals |
| Store tutorial progress in browser storage | No server state | Lost in private windows or a second browser; first launch is a property of the workspace, not the browser |
| Advance on "Next" for every step | Simpler | Lets a user skip doing the thing the step teaches; the user asked for a game |
| Keep `examples/` in the default scan folders | Repository demos visible in the app | First launch would not be empty |

## Consequences

Tutorial content is ordinary workspace content: it appears in Log and Activity, can be archived, and is not deleted on finish. Replays add more content with numbered names. The v0.2.0 practice endpoints (`/api/v1/tutorial-sessions`, `/api/v1/tutorial/{id}/…`) and the `/tutorial/{id}/` page are removed. Workspaces that relied on `examples/` appearing in the app need a `barloom-workspace.json`. Tutorial steps depend on stable `data-tour` attributes in the pages; changing a page's controls means checking the tour still finds them.
