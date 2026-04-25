"""Aggregate shadow-mode log and report divergences.

Usage:
    uv run python scripts/compare_shadow.py [path/to/shadow.jsonl]

Outputs counts and per-divergence detail to stdout.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def main(log_path: Path) -> int:
    if not log_path.exists():
        print(f"Log file not found: {log_path}", file=sys.stderr)
        return 1

    total = 0
    divergent: list[dict] = []
    decision_counts: Counter[tuple[str, str]] = Counter()

    with log_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            decision_counts[(rec["legacy_decision"], rec["shadow_decision"])] += 1
            if rec.get("divergence"):
                divergent.append(rec)

    print(f"Total records: {total}")
    if total:
        print(f"Divergences:   {len(divergent)} ({100 * len(divergent) / total:.1f}%)")
    else:
        print("No records.")
    print()
    print("Decision matrix (legacy → shadow):")
    for (legacy, shadow), count in sorted(decision_counts.items()):
        marker = " *" if legacy != shadow else ""
        print(f"  {legacy:12s} → {shadow:12s} : {count}{marker}")
    print()
    if divergent:
        print("First 10 divergences:")
        for rec in divergent[:10]:
            print(
                f"  campaign={rec['campaign_id']} beat={rec['beat_number']} "
                f"legacy={rec['legacy_decision']} shadow={rec['shadow_decision']} "
                f"reasons={rec.get('reasons', [])}"
            )
    return 0


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("logs/beat_progression_shadow.jsonl")
    sys.exit(main(path))
