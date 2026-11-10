"""Unattended CS2 launch benchmark. Needs admin (RAMMap flush, Process Monitor), so it
self-elevates with one UAC prompt. Launches CS2 through Steam, times it with
launch_timer.track (window + io_settled, no F9 needed), closes CS2, repeats.

  python bench/run_bench.py capture          fresh ProcMon file set -> data/fileset/cs2_launch.json
  python bench/run_bench.py run --runs 10    round-robin: cold, noshader, full, warm
  python bench/run_bench.py all --runs 10    capture, then run

Conditions:
  cold      flush, no prefetch
  noshader  flush, prefetch file set minus NVIDIA DXCache (CS2 files + driver DLLs)
  full      flush, prefetch whole file set
  warm      no flush, right after a launch (ceiling)

Writes data/bench/launches.jsonl (label bench-<cond>), prefetch_runs.jsonl,
bench_sessions.jsonl (start/end, so behaviour analysis can drop bench launches)
and run_bench.log. Steam must be running.
"""
import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
from launch_timer import find_process, save_record, track, wait_for_process  # noqa: E402

BLENDER_DIR = r"C:\Program Files\Blender Foundation\Blender 5.2"
BLEND_FILE = r"C:\bench\Coat_Render.blend"
# app profiles: exe, how to launch, app dir for the ProcMon file set, needs Steam
APPS = {
    "cs2": {"exe": "cs2.exe", "launch": ["steam://rungameid/730"],
            "dir": r"c:\program files (x86)\steam\steamapps\common\counter-strike global offensive", "steam": True},
    "blender": {"exe": "blender.exe", "launch": [BLENDER_DIR + r"\blender.exe", BLEND_FILE],
                "dir": BLENDER_DIR.lower(), "steam": False},
}
APP = "cs2"
EXE = "cs2.exe"
STEAM_URL = "steam://rungameid/730"
RAMMAP = ROOT / "tools" / "RAMMap64.exe"
PROCMON = ROOT / "tools" / "Procmon64.exe"
PROCMON_DIR = ROOT / "data" / "procmon"
FILESET = ROOT / "data" / "fileset" / "cs2_launch.json"


def set_app(name: str) -> None:
    """Switch the module globals to another app profile (cs2 stays the default)."""
    global APP, EXE, FILESET
    APP, EXE = name, APPS[name]["exe"]
    FILESET = ROOT / "data" / "fileset" / f"{name}_launch.json"
BENCH_DIR = ROOT / "data" / "bench"
LOG = BENCH_DIR / "run_bench.log"
SESSIONS = BENCH_DIR / "bench_sessions.jsonl"

FLUSH_FLAGS = ("-Ew", "-Em", "-Es", "-Et", "-E0")
SHADER_CACHE = r"\nvidia\dxcache"
BUDGET_GB = 24
# condition -> (flush first, prefetch exclude list or None for no prefetch)
CONDITIONS = {
    "cold": (True, None),
    "noshader": (True, [SHADER_CACHE]),
    "full": (True, []),
    "warm": (False, None),
}
LAUNCH_WAIT = 120      # s for Steam to start cs2.exe
LAUNCH_SETTLE = 180    # s max per launch measurement
MENU_LINGER = 10       # s on the main menu before closing, lets shader cache writes finish
CLOSE_TIMEOUT = 30     # s for a polite close before force kill
COOLDOWN = 20          # s after exit for Steam cloud sync / disk to calm down
FLUSH_SETTLE = 10      # s after flush or prefetch before launch


def log(msg: str) -> None:
    line = f"{datetime.now():%H:%M:%S} {msg}"
    print(line, flush=True)
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def is_admin() -> bool:
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def elevate() -> None:
    """Re-run this script elevated in a new console (one UAC prompt)."""
    params = " ".join(f'"{a}"' for a in [str(Path(__file__).resolve()), *sys.argv[1:]])
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, str(ROOT), 1)
    if rc <= 32:
        sys.exit(f"elevation refused or failed (ShellExecute {rc})")
    print(f"elevated window opened, progress in {LOG}")


def flush_cache() -> None:
    for flag in FLUSH_FLAGS:
        subprocess.run([str(RAMMAP), "/accepteula", flag], check=False)
    time.sleep(3)
    log(f"flushed, RAM used {psutil.virtual_memory().percent}%")


def prefetch(label: str, exclude: list) -> None:
    cmd = [sys.executable, str(ROOT / "bench" / "prefetch.py"), "--budget-gb", str(BUDGET_GB), "--label", label,
           "--fileset", str(FILESET)]
    for x in exclude:
        cmd += ["--exclude", x]
    subprocess.run(cmd, check=True)


def close_cs2(proc: psutil.Process) -> None:
    """Polite WM_CLOSE first so CS2 saves config and shader cache, force kill as fallback."""
    subprocess.run(["taskkill", "/IM", EXE], check=False, capture_output=True)
    try:
        proc.wait(CLOSE_TIMEOUT)
    except psutil.TimeoutExpired:
        log(f"{EXE} ignored close, killing")
        proc.kill()
        proc.wait(CLOSE_TIMEOUT)
    time.sleep(COOLDOWN)


def one_launch(label: str) -> dict:
    disk0 = psutil.disk_io_counters().read_bytes
    launch = APPS[APP]["launch"]
    if APPS[APP]["steam"]:
        os.startfile(launch[0])
    else:
        subprocess.Popen(launch)
    proc = wait_for_process(EXE, LAUNCH_WAIT)
    if not proc:
        raise RuntimeError(f"{EXE} did not start")
    marks = track(proc, EXE, LAUNCH_SETTLE, require_f9=False)
    record = save_record(label, "nvme", EXE, disk0, marks)
    time.sleep(MENU_LINGER)
    close_cs2(proc)
    log(f"{label}: window {record.get('window')} s, io_settled {record.get('io_settled')} s")
    return record


def capture() -> None:
    """ProcMon trace of one cold launch, then parse into the prefetch file set."""
    PROCMON_DIR.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now():%Y%m%d_%H%M}"
    pml, csv = PROCMON_DIR / f"{APP}_{stamp}.pml", PROCMON_DIR / f"{APP}_{stamp}.csv"
    if FILESET.exists():
        backup = FILESET.with_name(f"{APP}_launch_before_{stamp}.json")
        shutil.copy2(FILESET, backup)
        log(f"old file set kept as {backup.name}")
    flush_cache()
    subprocess.Popen([str(PROCMON), "/AcceptEula", "/Quiet", "/Minimized", "/NoFilter", "/BackingFile", str(pml)])
    time.sleep(5)
    log("procmon recording")
    try:
        one_launch("bench-capture")
    finally:
        subprocess.run([str(PROCMON), "/Terminate"], check=False)
        time.sleep(3)
    log("exporting procmon CSV (slow)")
    subprocess.run([str(PROCMON), "/AcceptEula", "/Quiet", "/OpenLog", str(pml), "/SaveAs", str(csv)], check=True)
    subprocess.run([sys.executable, str(ROOT / "bench" / "parse_procmon.py"), str(csv), "--target", EXE,
                    "--dir", APPS[APP]["dir"], "--out", str(FILESET)], check=True)
    pml.unlink(missing_ok=True)
    log(f"file set rebuilt from {csv.name}")


def run(runs: int) -> None:
    for i in range(1, runs + 1):
        for cond, (flush, exclude) in CONDITIONS.items():
            log(f"run {i}/{runs} {cond}")
            if flush:
                flush_cache()
            if exclude is not None:
                prefetch(f"bench-{cond}-{i}", exclude)
            time.sleep(FLUSH_SETTLE)
            one_launch(f"bench-{cond}" if APP == "cs2" else f"bench-{APP}-{cond}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["capture", "run", "all"])
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--app", choices=sorted(APPS), default="cs2")
    args = ap.parse_args()
    set_app(args.app)

    if not is_admin():
        elevate()
        return
    if APPS[APP]["steam"] and not find_process("steam.exe"):
        sys.exit("start Steam first")
    if find_process(EXE):
        sys.exit(f"{EXE} running, quit it first")

    start = datetime.now().astimezone().isoformat(timespec="seconds")
    status = "ok"
    log(f"=== bench {args.app} {args.mode} runs={args.runs}")
    try:
        if args.mode in ("capture", "all"):
            capture()
        if args.mode in ("run", "all"):
            run(args.runs)
    except BaseException:
        status = "failed"
        log(traceback.format_exc())
        raise
    finally:
        with SESSIONS.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"start": start, "end": datetime.now().astimezone().isoformat(timespec="seconds"),
                                 "mode": args.mode, "runs": args.runs, "app": args.app, "status": status}) + "\n")
        log(f"=== bench {args.mode} {status}")


if __name__ == "__main__":
    main()
