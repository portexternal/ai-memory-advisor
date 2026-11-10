"""Turn a Process Monitor CSV export into the CS2 launch file set:
data/fileset/cs2_launch.json = {"files": {path: [[offset, length], ...]}, totals}.
Only ReadFile events from the target process count. Ranges are merged and
rounded out to 64 KB so the prefetcher reads whole cache-manager blocks."""
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_IN = ROOT / "data" / "procmon" / "cs2.csv"
OUT = ROOT / "data" / "fileset" / "cs2_launch.json"
TARGET = "cs2.exe"
GAME_DIR = r"c:\program files (x86)\steam\steamapps\common\counter-strike global offensive"
BLOCK = 64 * 1024
DETAIL_RE = re.compile(r"Offset:\s*([\d,]+),\s*Length:\s*([\d,]+)")
csv.field_size_limit(1 << 30)


def merge(ranges: list) -> list:
    """Sort, align to BLOCK, merge overlaps/adjacent. Returns [[start, length], ...]."""
    aligned = sorted(((o // BLOCK) * BLOCK, -(-(o + n) // BLOCK) * BLOCK) for o, n in ranges)
    merged = []
    for s, e in aligned:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [[s, e - s] for s, e in merged]


def parse(path: Path) -> dict:
    per_file = defaultdict(list)
    rows = reads = 0
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rows += 1
            if row.get("Operation") != "ReadFile":
                continue
            # cs2's own reads, plus cache-manager/System page-ins of game files (mapped pak files)
            if row.get("Process Name", "").lower() != TARGET and not row.get("Path", "").lower().startswith(GAME_DIR):  # noqa
                continue
            m = DETAIL_RE.search(row.get("Detail", ""))
            if not m:
                continue
            offset, length = (int(x.replace(",", "")) for x in m.groups())
            per_file[row["Path"]].append((offset, length))
            reads += 1
    files = {p: merge(r) for p, r in per_file.items()}
    return {"rows": rows, "reads": reads, "files": files}


def main():
    global TARGET, GAME_DIR, OUT
    args = sys.argv[1:]
    opts = {}
    while len(args) >= 2 and args[-2].startswith("--"):
        opts[args[-2][2:]] = args[-1]
        args = args[:-2]
    TARGET = opts.get("target", TARGET).lower()
    GAME_DIR = opts.get("dir", GAME_DIR).lower()
    OUT = Path(opts["out"]) if "out" in opts else OUT
    src = Path(args[0]) if args else CSV_IN
    if not src.exists():
        sys.exit(f"missing {src}, run bench/capture_fileset.ps1 first")
    result = parse(src)
    files = result["files"]
    total = sum(n for ranges in files.values() for _, n in ranges)
    sizes = sorted(((sum(n for _, n in r), p) for p, r in files.items()), reverse=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "target": TARGET, "source": str(src), "block": BLOCK,
        "procmon_rows": result["rows"], "read_events": result["reads"],
        "file_count": len(files), "total_bytes": total,
        "files": dict(sorted(files.items())),
    }, indent=0), encoding="utf-8")
    print(f"{result['rows']:,} procmon rows, {result['reads']:,} cs2 reads, {len(files)} files, {total / 1e9:.2f} GB in ranges")
    for size, p in sizes[:15]:
        print(f"  {size / 1e6:8.1f} MB  {p}")
    print(f"written {OUT}")


if __name__ == "__main__":
    main()
