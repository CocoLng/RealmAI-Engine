"""Architectural guard: ``engine/`` must never import from ``ai/``.

The engine is pure deterministic Python. Shared I/O contracts live in
``engine.contracts``; ``ai/`` depends on ``engine``, never the reverse. This
test scans every engine source file's AST (so it catches ``TYPE_CHECKING``
imports too) and fails on any ``from ai ...`` / ``import ai`` line.
"""

from __future__ import annotations

import ast
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parents[2] / "engine"


def _engine_files() -> list[Path]:
    return [p for p in ENGINE_ROOT.rglob("*.py") if "__pycache__" not in p.parts]


def _is_ai_module(name: str) -> bool:
    return name == "ai" or name.startswith("ai.")


def test_engine_has_no_ai_imports() -> None:
    offenders: list[str] = []
    for path in _engine_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(ENGINE_ROOT.parent)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and _is_ai_module(node.module or ""):
                offenders.append(f"{rel}:{node.lineno} from {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_ai_module(alias.name):
                        offenders.append(f"{rel}:{node.lineno} import {alias.name}")

    assert not offenders, "engine/ must not import from ai/:\n" + "\n".join(offenders)
