"""Data-only risk model cards and point-in-time inference for daily bars."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Sequence

from ..market_calendar import FIRST_YEAR, sessions

FEATURES = ("log_vol_20", "log_atr_14")
CARD_SCHEMA = 1
_FILENAME = re.compile(r"risk_([0-9a-f]{16})\.json$")


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def load_card(path: Path) -> dict:
    """Read an immutable JSON card without importing any training library or code."""
    path = Path(path)
    match = _FILENAME.fullmatch(path.name)
    if not match:
        raise ValueError("A risk card filename must be risk_<16-digit-hash>.json")
    raw = path.read_bytes()
    if len(raw) > 1_000_000 or _digest(raw)[:16] != match.group(1):
        raise ValueError("Risk card content does not match its filename")
    card = json.loads(raw)
    if not isinstance(card, dict) or card.get("schema") != CARD_SCHEMA or card.get("kind") != "drop_risk_logistic":
        raise ValueError("Unsupported risk card schema or kind")
    if card.get("features") != list(FEATURES):
        raise ValueError("Risk card feature recipe is not supported")
    recipe = card.get("recipe")
    if not isinstance(recipe, dict) or recipe.get("decision_time") != "daily_close" or \
            recipe.get("entry_time") != "next_open" or \
            isinstance(recipe.get("horizon"), bool) or not isinstance(recipe.get("horizon"), int) or \
            not 1 <= recipe["horizon"] <= 20 or not 0 < _finite(recipe.get("drop"), "drop") < 1:
        raise ValueError("Risk card has an invalid label recipe")
    try:
        date.fromisoformat(card.get("information_end"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Risk card needs an ISO information_end date") from exc
    manifest = card.get("data_manifest")
    if not isinstance(manifest, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(manifest.get("sha256"))) or \
            not isinstance(manifest.get("source"), str) or not manifest["source"].strip() or \
            manifest.get("source_status") != "user_declared":
        raise ValueError("Risk card needs a data manifest with source and SHA-256")
    weights = card.get("weights")
    if not isinstance(weights, dict) or not all(isinstance(weights.get(key), list)
                                                and len(weights[key]) == len(FEATURES)
                                                for key in ("mean", "scale", "coef")):
        raise ValueError("Risk card weights do not match its features")
    for key in ("mean", "scale", "coef"):
        for value in weights[key]:
            _finite(value, key)
    if any(value <= 0 for value in weights["scale"]):
        raise ValueError("Risk card scales must be positive")
    _finite(weights.get("intercept"), "intercept")
    return {**card, "sha256": _digest(raw), "model_id": path.stem}


def save_card(directory: Path, card: dict) -> Path:
    """Write a content-addressed JSON card; refuse to replace existing bytes."""
    raw = (json.dumps(card, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"risk_{_digest(raw)[:16]}.json"
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("An existing risk card has the same filename but different content")
        return path
    with path.open("xb") as target:
        target.write(raw)
    load_card(path)
    return path


@lru_cache(maxsize=8192)
def _expected_days(start: date, end: date) -> tuple[date, ...]:
    return tuple(sorted(sessions(start, end)))


def risk_features(bars: Sequence) -> tuple[float, float] | None:
    """Use only the last 21 completed bars, including the current close."""
    if len(bars) < 21:
        return None
    window = [bars[index] for index in range(len(bars) - 21, len(bars))]
    days = [bar.date for bar in window]
    if days[0].year < FIRST_YEAR:
        return None
    if tuple(days) != _expected_days(days[0], days[-1]):
        return None
    if any(not all(math.isfinite(value) and value > 0 for value in
                   (bar.open, bar.high, bar.low, bar.close)) or bar.volume <= 0 for bar in window):
        return None
    returns = [math.log(window[index].close / window[index - 1].close) for index in range(1, 21)]
    mean = sum(returns) / len(returns)
    volatility = math.sqrt(sum((value - mean) ** 2 for value in returns) / len(returns))
    ranges = [max(window[index].high - window[index].low,
                  abs(window[index].high - window[index - 1].close),
                  abs(window[index].low - window[index - 1].close))
              for index in range(7, 21)]
    atr = sum(ranges) / 14 / window[-1].close
    return math.log(max(volatility, 1e-12)), math.log(max(atr, 1e-12))


def predict(card: dict, features: Sequence[float]) -> float:
    weights = card["weights"]
    z = weights["intercept"] + sum(coef * ((value - mean) / scale)
                                       for coef, value, mean, scale in zip(weights["coef"], features,
                                                                           weights["mean"], weights["scale"]))
    return 1 / (1 + math.exp(-z)) if z >= 0 else math.exp(z) / (1 + math.exp(z))
