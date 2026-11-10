"""No-admin launch benchmark (6/11/2026). Same idea as run_bench.py but runs as a normal user, so it
can go unattended with nobody at the PC to click UAC.

Differences from run_bench.py (state them in the thesis):
  cold flush  memory pressure: allocate and touch most of free RAM, then release, so Windows repurposes
              the standby list (file cache). RAMMap (admin) empties it directly; this evicts it indirectly.
  file set    no ProcMon (admin driver). Built from the files the app has open or memory-mapped during one
              launch, plus the document it opens. Whole files, no byte ranges.

  python bench/run_bench_user.py capture --app blender
  python bench/run_bench_user.py run --app blender --runs 5      round-robin: cold, full, warm
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
from launch_timer import save_record, track, wait_for_process  # noqa: E402
from run_bench import APPS  # noqa: E402

BENCH_DIR = ROOT / "data" / "bench"
LOG = BENCH_DIR / "run_bench_user.log"
SESSIONS = BENCH_DIR / "bench_sessions.jsonl"
LAUNCH_WAIT = 60
LAUNCH_SETTLE = 150
LINGER = 5
CLOSE_TIMEOUT = 30
COOLDOWN = 15
SETTLE = 8
KEEP_FREE_GB = -3.0     # negative = push this far past available RAM (into the pagefile) so protected standby pages go too
CHUNK_MB = 256
CONDITIONS = {"cold": (True, False), "full": (True, True), "warm": (False, False)}  # (flush, prefetch)


def log(msg: str) -> None:
    line = f"{datetime.now():%H:%M:%S} {msg}"
    print(line, flush=True)
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def pressure_flush() -> None:
    """Fill most of available RAM so the standby (file) cache gets repurposed, then free it.
    Filler is random (a 1 MB random block repeated): Windows memory compression would squeeze identical
    pages to nothing and leave the file cache untouched."""
    target = int(psutil.virtual_memory().available - KEEP_FREE_GB * 1e9)
    chunk = CHUNK_MB * 1024 * 1024
    block = os.urandom(1024 * 1024) * CHUNK_MB
    held = []
    try:
        while sum(len(b) for b in held) + chunk < target:
            held.append(bytearray(block))
            if psutil.swap_memory().percent > 85:
                break
        log(f"pressure flush held {sum(len(b) for b in held) / 1e9:.1f} GB")
    finally:
        held.clear()
    time.sleep(3)


def fileset_path(app: str) -> Path:
    return ROOT / "data" / "fileset" / f"{app}_launch_user.json"


def launch(app: str) -> psutil.Process:
    subprocess.Popen(APPS[app]["launch"])
    proc = wait_for_process(APPS[app]["exe"], LAUNCH_WAIT)
    if not proc:
        raise RuntimeError(f"{APPS[app]['exe']} did not start")
    return proc


def close(proc: psutil.Process, exe: str) -> None:
    subprocess.run(["taskkill", "/IM", exe], check=False, capture_output=True)
    try:
        proc.wait(CLOSE_TIMEOUT)
    except psutil.TimeoutExpired:
        log(f"{exe} ignored close, killing")
        proc.kill()
        proc.wait(CLOSE_TIMEOUT)
    time.sleep(COOLDOWN)


def capture(app: str) -> None:
    """One launch, sampling open files and memory maps until reads settle; write a whole-file set."""
    pressure_flush()
    proc = launch(app)
    seen = set(p for p in APPS[app]["launch"][1:] if Path(p).is_file())
    end = time.time() + 40
    while time.time() < end and proc.is_running():
        try:
            seen.update(f.path for f in proc.open_files())
            seen.update(m.path for m in proc.memory_maps() if m.path and Path(m.path).is_file())
        except psutil.Error:
            pass
        time.sleep(0.5)
    close(proc, APPS[app]["exe"])
    files = {}
    for p in sorted(seen):
        try:
            files[p] = [[0, Path(p).stat().st_size]]
        except OSError:
            continue
    total = sum(r[0][1] for r in files.values())
    out = fileset_path(app)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"target": APPS[app]["exe"], "method": "open_files+memory_maps, whole files",
                               "file_count": len(files), "total_bytes": total, "files": files}, indent=0),
                   encoding="utf-8")
    log(f"file set {len(files)} files, {total / 1e9:.2f} GB -> {out.name}")


def prefetch(app: str, label: str) -> None:
    subprocess.run([sys.executable, str(ROOT / "bench" / "prefetch.py"), "--budget-gb", "8", "--whole-files",
                    "--label", label, "--fileset", str(fileset_path(app))], check=True)


def one_launch(app: str, label: str) -> dict:
    disk0 = psutil.disk_io_counters().read_bytes
    proc = launch(app)
    marks = track(proc, APPS[app]["exe"], LAUNCH_SETTLE, require_f9=False)
    record = save_record(label, "nvme", APPS[app]["exe"], disk0, marks)
    time.sleep(LINGER)
    close(proc, APPS[app]["exe"])
    log(f"{label}: window {record.get('window')} s, io_settled {record.get('io_settled')} s, "
        f"disk read {record.get('system_read_mb', '?')} MB")
    return record


def run(app: str, runs: int) -> None:
    for i in range(1, runs + 1):
        for cond, (flush, pre) in CONDITIONS.items():
            log(f"run {i}/{runs} {cond}")
            if flush:
                pressure_flush()
            if pre:
                prefetch(app, f"user-{app}-{cond}-{i}")
            time.sleep(SETTLE)
            one_launch(app, f"user-{app}-{cond}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["capture", "run", "all"])
    ap.add_argument("--app", choices=sorted(APPS), default="blender")
    ap.add_argument("--runs", type=int, default=5)
    args = ap.parse_args()
    if APPS[args.app]["steam"]:
        sys.exit("steam apps not wired in the user bench yet, use run_bench.py")
    start = datetime.now().astimezone().isoformat(timespec="seconds")
    status = "ok"
    log(f"=== user bench {args.app} {args.mode} runs={args.runs}")
    try:
        if args.mode in ("capture", "all"):
            capture(args.app)
        if args.mode in ("run", "all"):
            run(args.app, args.runs)
    except BaseException:
        status = "failed"
        raise
    finally:
        with SESSIONS.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"start": start, "end": datetime.now().astimezone().isoformat(timespec="seconds"),
                                 "mode": f"user-{args.mode}", "runs": args.runs, "app": args.app,
                                 "status": status}) + "\n")
        log(f"=== user bench {status}")


if __name__ == "__main__":
    main()
