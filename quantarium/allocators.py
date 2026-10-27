"""Built-in allocators for composite and regime supercells.

An allocator decides how a Complex cell's money is split between its sleeves
at each daily close. ``allocate(context, sleeves)`` receives the context and
one ``Sleeve`` per script, in order, with that sleeve's own target weights and
its configured allocation, and returns one fraction per sleeve, each from 0 to
1, summing to at most 1. The engine then combines the sleeves' weights with
those fractions instead of the fixed allocations.
"""

from __future__ import annotations


class FixedAllocation:
    """The configured allocations, unchanged: the Lab's control for an allocator."""

    def allocate(self, context, sleeves):
        return [sleeve.allocation for sleeve in sleeves]


class TrendRegime:
    """All money to one sleeve while a symbol trades above its moving average, else to another.

    ``symbol`` must be one of the cell's symbols. ``risk_on`` and ``risk_off``
    are sleeve positions, counting from 0. Until ``window`` sessions of
    history exist, the money stays with ``risk_off``.
    """

    def __init__(self, symbol: str, window: int = 50, risk_on: int = 0, risk_off: int = 1) -> None:
        if not isinstance(window, int) or window < 2:
            raise ValueError("TrendRegime.window must be a whole number of at least 2")
        self.symbol, self.window, self.risk_on, self.risk_off = str(symbol).upper(), window, risk_on, risk_off

    def allocate(self, context, sleeves):
        if self.symbol not in context.history:
            raise ValueError(f"TrendRegime.symbol {self.symbol} is not one of the cell's symbols")
        for position in (self.risk_on, self.risk_off):
            if not isinstance(position, int) or not 0 <= position < len(sleeves):
                raise ValueError(f"TrendRegime sleeve {position} does not exist; the cell has {len(sleeves)}")
        closes = [bar.close for bar in context.history[self.symbol]][-self.window:]
        chosen = self.risk_off
        if len(closes) == self.window and closes[-1] > sum(closes) / len(closes):
            chosen = self.risk_on
        return [1.0 if index == chosen else 0.0 for index in range(len(sleeves))]
