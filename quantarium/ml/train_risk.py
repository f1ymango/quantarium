"""Train a disclosed volatility baseline in a separate process.

Only this module imports the optional ML libraries. The resulting model is
JSON numbers, so simulations never unpickle objects or import training code.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from ..market_calendar import FIRST_YEAR
from ..model import Bar
from .card import FEATURES, _expected_days, risk_features, save_card

REQUIRED = {"date", "symbol", "open", "high", "low", "close", "volume"}


def _bars(path: Path) -> dict[str, list[Bar]]:
    grouped = defaultdict(list)
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if not REQUIRED <= set(reader.fieldnames or []):
            raise ValueError("Training data needs date,symbol,open,high,low,close,volume columns")
        for row in reader:
            symbol = row["symbol"].strip().upper()
            if not symbol:
                raise ValueError("Training data has an empty symbol")
            bar = Bar(date.fromisoformat(row["date"]), symbol,
                      *(float(row[key]) for key in ("open", "high", "low", "close")),
                      int(row["volume"]))
            if not all(math.isfinite(value) and value > 0 for value in
                       (bar.open, bar.high, bar.low, bar.close)) or bar.volume <= 0:
                raise ValueError(f"Invalid training bar for {symbol} on {bar.date}")
            grouped[symbol].append(bar)
    if not grouped:
        raise ValueError("Training data has no bars")
    for symbol, bars in grouped.items():
        bars.sort(key=lambda item: item.date)
        if len({bar.date for bar in bars}) != len(bars):
            raise ValueError(f"Training data repeats a date for {symbol}")
    return dict(grouped)


def examples(data: dict[str, list[Bar]], through: date, horizon: int, drop: float):
    """Return features and labels whose final outcome is known by `through`."""
    values, labels, ends, decisions = [], [], [], []
    for bars in data.values():
        for index in range(20, len(bars) - horizon):
            end = bars[index + horizon].date
            if end > through:
                break
            if bars[index - 20].date.year < FIRST_YEAR:
                continue
            if tuple(bar.date for bar in bars[index - 20:index + horizon + 1]) != \
                    _expected_days(bars[index - 20].date, end):
                continue
            features = risk_features(bars[index - 20:index + 1])
            if features is None:
                continue
            entry = bars[index + 1].open
            worst = min(bar.low for bar in bars[index + 1:index + horizon + 1])
            values.append(features)
            labels.append(int(worst / entry - 1 < -drop))
            ends.append(end)
            decisions.append(bars[index].date)
    return values, labels, ends, decisions


def _fit_python(values: list[tuple[float, float]], labels: list[int]) -> tuple[dict, dict]:
    """Deterministic, small-feature batch logistic fit without an ML runtime."""
    count = len(values)
    means = [sum(row[index] for row in values) / count for index in range(len(FEATURES))]
    scales = [max(math.sqrt(sum((row[index] - means[index]) ** 2 for row in values) / count), 1e-12)
              for index in range(len(FEATURES))]
    positive = sum(labels) / count
    intercept = math.log(positive / (1 - positive))
    coefficients = [0.0] * len(FEATURES)
    # Two standardized inputs keep this bounded and deterministic. A separate
    # process owns the CPU work; the simulator never fits a model in-line.
    for _ in range(80):
        gradient = [0.0] * len(FEATURES)
        bias_gradient = 0.0
        for row, label in zip(values, labels):
            scaled = [(value - mean) / scale for value, mean, scale in zip(row, means, scales)]
            score = intercept + sum(weight * feature for weight, feature in zip(coefficients, scaled))
            probability = 1 / (1 + math.exp(-score)) if score >= 0 else math.exp(score) / (1 + math.exp(score))
            difference = probability - label
            bias_gradient += difference
            for index, feature in enumerate(scaled):
                gradient[index] += difference * feature
        intercept -= 0.5 * bias_gradient / count
        coefficients = [weight - 0.5 * (slope / count + 0.01 * weight)
                        for weight, slope in zip(coefficients, gradient)]
    return ({"mean": means, "scale": scales, "coef": coefficients,
             "intercept": intercept},
            {"algorithm": "standard_library_batch_logistic", "iterations": 80,
             "learning_rate": 0.5, "l2": 0.01, "python": sys.version.split()[0]})


def _fit_optional(values: list[tuple[float, float]], labels: list[int]) -> tuple[dict, dict] | None:
    try:
        import numpy as np
        import sklearn
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        return None
    x = np.asarray(values, dtype=float)
    y = np.asarray(labels, dtype=int)
    scaler = StandardScaler().fit(x)
    classifier = LogisticRegression(solver="lbfgs", max_iter=1000, random_state=0).fit(
        scaler.transform(x), y)
    return ({"mean": [float(v) for v in scaler.mean_],
             "scale": [float(v) for v in scaler.scale_],
             "coef": [float(v) for v in classifier.coef_[0]],
             "intercept": float(classifier.intercept_[0])},
            {"algorithm": "standard_scaler_logistic_regression", "numpy": np.__version__,
             "scikit_learn": sklearn.__version__, "random_state": 0})


def train(data_path: Path, output_dir: Path, *, through: date, source: str,
          horizon: int = 5, drop: float = 0.05) -> dict:
    if not source.strip() or len(source) > 100:
        raise ValueError("source must describe the input provider in 1-100 characters")
    if not 1 <= horizon <= 20 or not 0 < drop < 1:
        raise ValueError("horizon must be 1-20 and drop must be between 0 and 1")
    data_path = Path(data_path).resolve()
    hasher = hashlib.sha256()
    with data_path.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    data = _bars(data_path)
    values, labels, ends, decisions = examples(data, through, horizon, drop)
    if len(values) < 40 or min(labels.count(0), labels.count(1)) < 5:
        raise ValueError("Training needs at least 40 complete examples and five of each outcome")
    fitted = _fit_optional(values, labels)
    if fitted is None and len(values) > 100_000:
        raise ValueError("More than 100,000 training rows require the optional [ml] extra")
    weights, training = fitted or _fit_python(values, labels)
    card = {
        "schema": 1, "kind": "drop_risk_logistic", "created_utc": datetime.now(timezone.utc).isoformat(),
        "features": list(FEATURES),
        "recipe": {"decision_time": "daily_close", "entry_time": "next_open",
                   "horizon": horizon, "drop": drop},
        "data_manifest": {"source": source.strip(), "source_status": "user_declared",
                          "file_name": data_path.name, "sha256": digest,
                          "symbols": sorted(data), "training_rows": len(values),
                          "positive_rows": sum(labels),
                          "last_feature_date": max(decisions).isoformat()},
        "information_end": max(ends).isoformat(),
        "weights": weights,
        "training": training,
    }
    path = save_card(output_dir, card)
    return {"model_card": str(path), "information_end": card["information_end"],
            "training_rows": len(values), "positive_rows": sum(labels),
            "source_status": "user_declared"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a data-only drop-risk baseline")
    parser.add_argument("data", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--through", type=date.fromisoformat, required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--drop", type=float, default=0.05)
    args = parser.parse_args()
    print(json.dumps(train(args.data, args.output_dir, through=args.through, source=args.source,
                           horizon=args.horizon, drop=args.drop)))


if __name__ == "__main__":
    main()
