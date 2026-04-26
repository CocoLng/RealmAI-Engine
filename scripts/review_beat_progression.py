"""Aggregate beat progression logs into a per-campaign report.

Usage:
    uv run python scripts/review_beat_progression.py [campaign_id] [path/to/log.jsonl]

Without args, summarizes all campaigns in the default log file.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


def main(campaign_filter: str | None, log_path: Path) -> int:
    if not log_path.exists():
        print(f"No log at {log_path}", file=sys.stderr)
        return 1

    by_campaign: dict[str, list[dict]] = defaultdict(list)
    with log_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            cid = rec.get("campaign_id")
            if not cid:
                continue
            if campaign_filter and cid != campaign_filter:
                continue
            by_campaign[cid].append(rec)

    for cid, records in sorted(by_campaign.items()):
        print(f"\n=== Campaign {cid} ===")
        decision_counts: dict[str, int] = defaultdict(int)
        per_beat_progress: dict[int, list[int]] = defaultdict(list)
        judge_calls = 0
        judge_passed = 0
        for r in records:
            decision_counts[r["decision"]] += 1
            per_beat_progress[r["beat_number"]].append(r.get("progress_score", 0))
            if r.get("judge_confidence") is not None:
                judge_calls += 1
                if r.get("judge_passed"):
                    judge_passed += 1
        total = sum(decision_counts.values())
        print(f"Total decisions: {total}")
        for d, c in sorted(decision_counts.items()):
            print(f"  {d:12s}: {c} ({100 * c / total:.0f}%)")
        if judge_calls:
            print(f"Judge calls: {judge_calls} (pass rate: {100 * judge_passed / judge_calls:.0f}%)")
        print("Beats with score < 50% peak:")
        for beat_n, scores in sorted(per_beat_progress.items()):
            if max(scores) < 50:
                print(f"  - Beat {beat_n}: peak {max(scores)}%")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    cid = args[0] if args and not args[0].endswith(".jsonl") else None
    path_arg = args[-1] if args and args[-1].endswith(".jsonl") else "logs/beat_progression.jsonl"
    sys.exit(main(cid, Path(path_arg)))
