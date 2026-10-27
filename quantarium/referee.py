"""Replaying decisions made outside Quantarium on Quantarium's own engine.

A cell whose sleeve names a decision file runs ``ReplayDecisions`` instead of
a script. At each close it returns that date's decision from the file, or
holds when the file has none; fills, costs, and portfolio rules are the
engine's, as for any cell. See ``docs/plans/v0.6.x-referee.md``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .decisions import load_decisions
from .model import Decision

ENTRYPOINT = "quantarium.referee:ReplayDecisions"
INBOX_ENTRYPOINT = "quantarium.referee:ReplayInbox"
DECIDER_ENTRYPOINT = "quantarium.referee:LiveDecider"


class ReplayDecisions:
    """Return each session's decision from a decision file.

    ``sha256`` pins the file's bytes: a file changed after the run was set
    up, or after a Lab registration, stops the run instead of replaying
    other decisions.
    ``symbol`` is set for a Simple cell, whose strategy returns one weight.
    """

    def __init__(self, decisions: str, sha256: str, symbol: str | None = None) -> None:
        self.file = load_decisions(Path(decisions))
        if self.file.sha256 != sha256:
            raise ValueError(f"The decision file {Path(decisions).name} changed after this run was set up; "
                             "run the cell again to use the new file")
        self.symbol = symbol

    def on_bar(self, context) -> Decision:
        entry = self.file.entry(context.date)
        if entry is None:
            return Decision(weights=None)
        weights = None
        if entry.weights is not None:
            weights = float(entry.weights.get(self.symbol, 0.0)) if self.symbol else dict(entry.weights)
        return Decision(weights=weights, orders=entry.orders, notes={"note": entry.note} if entry.note else None)


class ReplayInbox(ReplayDecisions):
    """Return each session's on-time decision from a forward inbox.

    Only decisions that arrived before the next session opened count. Feed
    Batch reads the inbox afresh at each advance, so new submissions take
    part. A Lab registration freezes the inbox with ``until_bytes``.
    """

    def __init__(self, inbox: str, symbol: str | None = None, until_bytes: int | None = None,
                 sha256: str | None = None) -> None:
        from .inbox import read

        if until_bytes is not None and pin_prefix(Path(inbox), until_bytes) != sha256:
            raise ValueError(f"The decision inbox {Path(inbox).stem} changed before its registered end")
        self.file = read(Path(inbox), until_bytes)
        self.symbol = symbol


def pin_prefix(path: Path, until_bytes: int) -> str:
    """The SHA-256 of a file's first ``until_bytes`` bytes."""
    return hashlib.sha256(path.read_bytes()[:until_bytes]).hexdigest()


class LiveDecider:
    """Run a registered decider for this run, one session at a time, recording its tape.

    The decider sees only data up to each session, so it cannot look ahead
    through Quantarium. ``fingerprint`` pins its command, variables, and cutoff
    when a Lab registration asks for it.
    """

    def __init__(self, decider: str, workspace: str, tapes: str, symbols: list[str], symbol: str | None = None,
                 fingerprint: str | None = None) -> None:
        from .deciders import DeciderProcess, fingerprint as current, get, tape_path

        item = get(Path(workspace), decider)
        if fingerprint is not None and current(item) != fingerprint:
            raise ValueError(f"The decider {decider} changed after registration")
        self.process = DeciderProcess(item, symbols, tape_path(Path(tapes), decider))
        self.symbol = symbol

    def on_bar(self, context) -> Decision:
        entry = self.process.decide(context)
        weights = None
        if entry.weights is not None:
            weights = float(entry.weights.get(self.symbol, 0.0)) if self.symbol else dict(entry.weights)
        return Decision(weights=weights, orders=entry.orders, notes={"note": entry.note} if entry.note else None)
