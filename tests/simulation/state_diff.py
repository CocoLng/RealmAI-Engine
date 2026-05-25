"""state_diff — recursive dict diff producing {dotted.path: [old, new]} pairs."""

from __future__ import annotations

from typing import Any

_MISSING = object()


def state_diff(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    prefix: str = "",
) -> dict[str, list[Any]]:
    """Compute a recursive diff between two dicts.

    Returns a flat dict of {dotted.path: [old, new]} for every leaf that changed.
    Nested dicts are walked recursively. Lists are treated as leaves (compared
    by equality, not element-wise).

    Keys present in only one side use None for the missing side.
    """
    result: dict[str, list[Any]] = {}
    all_keys: set[str] = set(before) | set(after)
    for key in all_keys:
        path = f"{prefix}.{key}" if prefix else key
        b = before.get(key, _MISSING)
        a = after.get(key, _MISSING)

        # Both are dicts → recurse
        if isinstance(b, dict) and isinstance(a, dict):
            result.update(state_diff(b, a, prefix=path))
            continue

        # Convert _MISSING to None for the output
        b_val: Any = None if b is _MISSING else b
        a_val: Any = None if a is _MISSING else a

        if b_val == a_val:
            continue
        result[path] = [b_val, a_val]
    return result
