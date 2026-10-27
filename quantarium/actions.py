"""Conservative translation of Alpaca corporate actions into account events."""

from __future__ import annotations

import math
from datetime import date

from .model import CorporateAction


SUPPORTED_TYPES = {"cash_dividends", "forward_splits", "reverse_splits"}


def normalize_feed_actions(
    records: list[dict], start_date: date, through: date
) -> list[CorporateAction]:
    """Accept only actions whose account treatment is explicitly modeled."""
    result: list[CorporateAction] = []
    seen_ids: set[str] = set()
    for record in records:
        kind = record.get("type")
        symbol = record.get("symbol")
        source_id = record.get("id")
        if not isinstance(source_id, str) or not source_id or source_id in seen_ids:
            raise ValueError("Corporate action needs a unique nonempty ID")
        seen_ids.add(source_id)
        if not isinstance(symbol, str) or not symbol:
            raise ValueError(f"Corporate action {source_id} needs a symbol")
        if kind not in SUPPORTED_TYPES:
            raise ValueError(f"Unsupported corporate action {kind} for {symbol} ({source_id})")
        try:
            process_date = date.fromisoformat(record["process_date"])
            ex_date = date.fromisoformat(record["ex_date"])
            if kind == "cash_dividends":
                payment_date = date.fromisoformat(record["payable_date"])
                value = float(record["rate"])
            else:
                payment_date = ex_date
                value = float(record["new_rate"]) / float(record["old_rate"])
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
            raise ValueError(f"Incomplete corporate action {source_id}: {exc}") from exc
        if not math.isfinite(value) or value <= 0 or payment_date < ex_date:
            raise ValueError(f"Invalid corporate-action terms for {source_id}")
        if record.get("currency", "USD") not in {"USD", "usd", None}:
            raise ValueError(f"Non-USD corporate action {source_id} is not modeled")
        if kind == "cash_dividends":
            if record.get("foreign") not in (None, False):
                raise ValueError(f"Foreign dividend withholding is not modeled for {source_id}")
            if record.get("due_bill_on_date") or record.get("due_bill_off_date"):
                raise ValueError(f"Due-bill dividend is not modeled for {source_id}")
            normalized_kind = "dividend"
        else:
            if record.get("due_bill_redemption_date"):
                raise ValueError(f"Due-bill split is not modeled for {source_id}")
            normalized_kind = "split"
        if ex_date < start_date or ex_date > through:
            continue
        result.append(CorporateAction(payment_date, symbol, normalized_kind,
                                      value, ex_date, source_id, process_date))
    by_ex_date: dict[tuple[date, str], list[CorporateAction]] = {}
    for action in result:
        by_ex_date.setdefault((action.ex_date, action.symbol), []).append(action)
    for (ex_date, symbol), same_day in by_ex_date.items():
        if len(same_day) > 1 and any(action.kind == "split" for action in same_day):
            raise ValueError(f"Mixed or multiple splits for {symbol} on {ex_date} are not modeled")
    return sorted(result, key=lambda action: (action.ex_date, action.symbol,
                                               action.source_id or ""))
