"""JSON reading for untrusted input: no NaN or infinities, and bounded nesting."""

from __future__ import annotations

import json
import math

# Deeper JSON than this is refused: no request or file needs it, and deeper
# values can exhaust the interpreter's recursion limit in json, copy, or a handler.
MAX_DEPTH = 64


def refuse_constant(name: str) -> float:
    # NaN and Infinity are not JSON, and NaN passes every bound check unnoticed.
    raise ValueError(f"{name} is not a number Quantarium accepts")


def finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"{text} is too large a number")
    return value


def too_deep(value: object, limit: int = MAX_DEPTH) -> bool:
    """Whether nested lists and objects go deeper than ``limit``, checked without recursion."""
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, (dict, list)):
            if depth > limit:
                return True
            stack.extend((child, depth + 1) for child in (item.values() if isinstance(item, dict) else item))
    return False


def loads(data: str | bytes, limit: int = MAX_DEPTH) -> object:
    """Parse JSON strictly; every problem is a ValueError."""
    try:
        value = json.loads(data, parse_constant=refuse_constant, parse_float=finite_float)
    except RecursionError:
        raise ValueError("JSON is nested too deeply") from None
    if too_deep(value, limit):
        raise ValueError(f"JSON is nested more than {limit} levels deep")
    return value
