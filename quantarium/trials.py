"""Statistics that account for how many tries a result took.

- The probabilistic Sharpe ratio (Bailey and López de Prado, 2012): the
  chance a strategy's true Sharpe ratio is above a benchmark, given the
  sample's length, skewness, and kurtosis.
- The deflated Sharpe ratio (Bailey and López de Prado, 2014): the same,
  against the Sharpe ratio the best of N attempts would reach by chance.
- The probability of backtest overfitting (Bailey, Borwein, López de Prado,
  and Zhu, 2017), by combinatorially symmetric cross-validation: how often
  the attempt that looks best on half the data ranks below the median on the
  other half.

All Sharpe ratios here are per session (daily) unless named annual. These
statistics assume the attempts Quantarium saw are all the attempts.
"""

from __future__ import annotations

import itertools
import json
import math
import sqlite3
from contextlib import closing
from pathlib import Path
from statistics import NormalDist

SESSIONS_PER_YEAR = 252
EULER_GAMMA = 0.5772156649015329
_NORMAL = NormalDist()
# The deflated Sharpe ratio a supported verdict needs.
DSR_THRESHOLD = 0.95
PBO_MIN_TRIALS = 4
PBO_BLOCKS = 8


def daily_returns(database: Path, run_id: str, cell: str) -> list[tuple[str, float]]:
    """A cell's dated daily returns in a Static run, from its saved valuations."""
    with closing(sqlite3.connect(f"file:{Path(database).as_posix()}?mode=ro", uri=True)) as connection:
        row = connection.execute("SELECT initial_cash FROM cells WHERE run_id=? AND name=?", (run_id, cell)).fetchone()
        if row is None:
            return []
        previous = float(row[0])
        result = []
        for day, payload in connection.execute(
                "SELECT date, payload_json FROM events WHERE run_id=? AND cell_name=? AND kind='valuation' "
                "ORDER BY sequence", (run_id, cell)):
            equity = float(json.loads(payload)["equity"])
            if previous > 0 and math.isfinite(equity):
                result.append((day, equity / previous - 1))
            previous = equity
        return result


def moments(returns: list[float]) -> dict | None:
    """Mean, sample standard deviation, skewness, and (non-excess) kurtosis; None when undefined."""
    n = len(returns)
    if n < 3:
        return None
    mean = sum(returns) / n
    variance = sum((value - mean) ** 2 for value in returns) / (n - 1)
    if variance <= 0:
        return None
    deviation = math.sqrt(variance)
    population = sum((value - mean) ** 2 for value in returns) / n
    skew = sum((value - mean) ** 3 for value in returns) / n / population ** 1.5
    kurtosis = sum((value - mean) ** 4 for value in returns) / n / population ** 2
    return {"n": n, "mean": mean, "deviation": deviation, "sharpe": mean / deviation, "skew": skew,
            "kurtosis": kurtosis}


def probabilistic_sharpe(sharpe: float, n: int, skew: float, kurtosis: float, benchmark: float = 0.0) -> float:
    """The chance the true per-session Sharpe ratio exceeds ``benchmark``."""
    spread = 1 - skew * sharpe + (kurtosis - 1) / 4 * sharpe ** 2
    if spread <= 0 or n < 2:
        return 0.0
    return _NORMAL.cdf((sharpe - benchmark) * math.sqrt(n - 1) / math.sqrt(spread))


def expected_maximum_sharpe(trials: int, variance: float) -> float:
    """The Sharpe ratio the best of ``trials`` attempts reaches by chance, if none has any skill."""
    if trials < 2 or variance <= 0:
        return 0.0
    return math.sqrt(variance) * ((1 - EULER_GAMMA) * _NORMAL.inv_cdf(1 - 1 / trials)
                                  + EULER_GAMMA * _NORMAL.inv_cdf(1 - 1 / (trials * math.e)))


def deflated_sharpe(returns: list[float], trials: int, trial_sharpes: list[float]) -> dict | None:
    """The probabilistic and deflated Sharpe ratios of one return series, and what they rested on.

    The spread of Sharpe ratios across attempts comes from the attempts seen,
    when there are at least two; otherwise from this series' own sampling
    error, which is what attempts without skill would scatter by.
    """
    stats = moments(returns)
    if stats is None:
        return None
    sharpe, n = stats["sharpe"], stats["n"]
    if len(trial_sharpes) >= 2:
        mean = sum(trial_sharpes) / len(trial_sharpes)
        variance = sum((value - mean) ** 2 for value in trial_sharpes) / (len(trial_sharpes) - 1)
        source = "attempts"
    else:
        variance = (1 + sharpe ** 2 / 2) / (n - 1)
        source = "sampling error"
    benchmark = expected_maximum_sharpe(trials, variance)
    return {"sessions": n, "sharpe_annual": sharpe * math.sqrt(SESSIONS_PER_YEAR),
            "skew": stats["skew"], "kurtosis": stats["kurtosis"],
            "psr": probabilistic_sharpe(sharpe, n, stats["skew"], stats["kurtosis"]),
            "dsr": probabilistic_sharpe(sharpe, n, stats["skew"], stats["kurtosis"], benchmark),
            "trials": trials, "expected_max_sharpe_annual": benchmark * math.sqrt(SESSIONS_PER_YEAR),
            "variance_source": source}


def overfitting_probability(series: dict[str, list[tuple[str, float]]], blocks: int = PBO_BLOCKS) -> dict:
    """The probability of backtest overfitting for attempts on the same sessions.

    ``series`` maps each attempt to its dated daily returns. Only sessions all
    attempts share are used.
    """
    if len(series) < PBO_MIN_TRIALS:
        return {"pbo": None, "reason": f"needs at least {PBO_MIN_TRIALS} attempts on shared data", "attempts": len(series)}
    shared = sorted(set.intersection(*(set(day for day, _ in values) for values in series.values())))
    if len(shared) < blocks * 5:
        return {"pbo": None, "reason": f"needs at least {blocks * 5} shared sessions", "attempts": len(series)}
    names = sorted(series)
    columns = {name: dict(series[name]) for name in names}
    size = len(shared) // blocks
    chunks = [shared[index * size:(index + 1) * size] for index in range(blocks)]
    logits = []
    for chosen in itertools.combinations(range(blocks), blocks // 2):
        inside = [day for index in chosen for day in chunks[index]]
        outside = [day for index in range(blocks) if index not in chosen for day in chunks[index]]

        def score(name: str, days: list[str]) -> float:
            values = [columns[name][day] for day in days]
            stats = moments(values)
            return stats["sharpe"] if stats else 0.0

        best = max(names, key=lambda name: score(name, inside))
        outside_scores = {name: score(name, outside) for name in names}
        rank = 1 + sum(outside_scores[name] < outside_scores[best] for name in names)
        relative = rank / (len(names) + 1)
        logits.append(math.log(relative / (1 - relative)))
    return {"pbo": sum(value <= 0 for value in logits) / len(logits), "attempts": len(names),
            "sessions": len(shared), "combinations": len(logits), "reason": None}
