"""User-mode prefetcher. Reads the launch file set (data/fileset/cs2_launch.json)
through ordinary buffered file reads so the Windows cache manager keeps the pages
in the standby list. No driver, no privilege. Stops at --budget-gb.

Usage: python bench/prefetch.py [--budget-gb 8] [--whole-files] [--dry-run]
Appends a record to data/bench/prefetch_runs.jsonl."""
import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent.parent
FILESET = ROOT / "data" / "fileset" / "cs2_launch.json"
OUT = ROOT / "data" / "bench" / "prefetch_runs.jsonl"
CHUNK = 4 * 1024 * 1024


def read_ranges(path: str, ranges: list, whole: bool) -> int:
    """Read the given [offset, length] ranges (or the whole file). Returns bytes read."""
    done = 0
    try:
        size = os.path.getsize(path)
        with open(path, "rb", buffering=0) as fh:
            for offset, length in ([[0, size]] if whole else ranges):
                fh.seek(offset)
                remaining = min(length, max(size - offset, 0))
                while remaining > 0:
                    data = fh.read(min(CHUNK, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    done += len(data)
    except OSError:
        return done
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget-gb", type=float, default=8.0, help="stop after this many GB")
    ap.add_argument("--whole-files", action="store_true", help="read entire files instead of recorded ranges")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--label", default="manual")
    ap.add_argument("--exclude", action="append", default=[], help="skip paths containing this (case-insensitive), repeatable")
    ap.add_argument("--fileset", default=str(FILESET), help="launch file set json (default cs2)")
    args = ap.parse_args()

    fileset = json.loads(Path(args.fileset).read_text(encoding="utf-8"))
    excludes = [x.lower() for x in args.exclude]
    files = {p: r for p, r in fileset["files"].items() if not any(x in p.lower() for x in excludes)}
    budget = int(args.budget_gb * 1e9)
    print(f"{len(files)} files, {fileset['total_bytes'] / 1e9:.2f} GB recorded, budget {args.budget_gb} GB")
    if args.dry_run:
        return

    proc = psutil.Process()
    disk0, cpu0, t0 = psutil.disk_io_counters().read_bytes, proc.cpu_times(), time.perf_counter()
    total, missing = 0, 0
    for path, ranges in sorted(files.items(), key=lambda kv: -sum(n for _, n in kv[1])):
        if total >= budget:
            break
        if not os.path.exists(path):
            missing += 1
            continue
        total += read_ranges(path, ranges, args.whole_files)
    elapsed = time.perf_counter() - t0
    cpu1 = proc.cpu_times()
    record = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"), "label": args.label,
        "files": len(files), "missing": missing, "read_gb": round(total / 1e9, 2),
        "physical_read_gb": round((psutil.disk_io_counters().read_bytes - disk0) / 1e9, 2),
        "seconds": round(elapsed, 1), "mb_per_s": round(total / 1e6 / max(elapsed, 1e-3)),
        "cpu_seconds": round((cpu1.user - cpu0.user) + (cpu1.system - cpu0.system), 2),
        "whole_files": args.whole_files, "budget_gb": args.budget_gb, "exclude": args.exclude,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
