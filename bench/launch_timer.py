"""Launch latency timer for one game launch. Waits for the target exe to appear,
then records markers:
  t0            process creation (psutil create_time)
  window        first visible titled window owned by the process
  io_settled    process disk reads < IO_IDLE_MBPS for IO_IDLE_SECONDS after the window
  f9            the moment the user presses F9 (= "main menu is on screen")
Plus disk bytes read, page faults, RAM. Appends one JSON line to data/bench/launches.jsonl.

Usage: python bench/launch_timer.py --label cold --disk nvme [--exe cs2.exe] [--wait 600]
Press F9 when the main menu is visible. Script exits after F9 or after --settle seconds.
"""
import argparse
import ctypes
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from logger.winapi import windowed_process_names  # noqa: E402

OUT = ROOT / "data" / "bench" / "launches.jsonl"
VK_F9 = 0x78
POLL = 0.25
IO_IDLE_MBPS = 1.0
IO_IDLE_SECONDS = 3.0


def f9_pressed() -> bool:
    return bool(ctypes.windll.user32.GetAsyncKeyState(VK_F9) & 0x8000)


def find_process(exe: str):
    for proc in psutil.process_iter(["name"]):
        if (proc.info["name"] or "").lower() == exe:
            return proc
    return None


def wait_for_process(exe: str, wait: float):
    print(f"waiting up to {wait:.0f}s for {exe} ... launch the game now", flush=True)
    deadline = time.time() + wait
    while time.time() < deadline:
        proc = find_process(exe)
        if proc:
            return proc
        time.sleep(0.05)
    return None


def safe_io(proc):
    try:
        return proc.io_counters().read_bytes
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def safe_faults(proc):
    try:
        return proc.memory_info().num_page_faults
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def track(proc, exe: str, settle: float, require_f9: bool = True) -> dict:
    t0 = proc.create_time()
    marks = {}
    last_read, last_t, idle_since = safe_io(proc) or 0, time.time(), None
    peak_read = 0
    deadline = time.time() + settle
    while time.time() < deadline and proc.is_running():
        now = time.time()
        if "window" not in marks and exe in windowed_process_names():
            marks["window"] = now - t0
            print(f"  window visible at {marks['window']:.2f}s", flush=True)
        read = safe_io(proc)
        if read is not None:
            mbps = (read - last_read) / max(now - last_t, 1e-3) / 1e6
            peak_read = max(peak_read, read)
            if "window" in marks and "io_settled" not in marks:
                idle_since = idle_since if mbps < IO_IDLE_MBPS and idle_since else (None if mbps >= IO_IDLE_MBPS else now)
                if idle_since and now - idle_since >= IO_IDLE_SECONDS:
                    marks["io_settled"] = idle_since - t0
                    print(f"  disk reads settled at {marks['io_settled']:.2f}s", flush=True)
            last_read, last_t = read, now
        if "f9" not in marks and f9_pressed():
            marks["f9"] = now - t0
            print(f"  F9 pressed at {marks['f9']:.2f}s  <- main menu", flush=True)
        if "io_settled" in marks and ("f9" in marks or not require_f9):
            break
        time.sleep(POLL)
    marks["process_read_mb"] = round(peak_read / 1e6, 1)
    marks["page_faults"] = safe_faults(proc)
    return marks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="cold | warm | cold-ish | ours | oracle ...")
    ap.add_argument("--disk", default="nvme")
    ap.add_argument("--exe", default="cs2.exe")
    ap.add_argument("--wait", type=float, default=600)
    ap.add_argument("--settle", type=float, default=180)
    args = ap.parse_args()
    exe = args.exe.lower()

    if find_process(exe):
        sys.exit(f"{exe} already running, quit it first")
    disk0 = psutil.disk_io_counters().read_bytes
    proc = wait_for_process(exe, args.wait)
    if not proc:
        sys.exit("timed out, no launch seen")
    print(f"{exe} pid {proc.pid} started, press F9 when the main menu is visible", flush=True)
    marks = track(proc, exe, args.settle)
    save_record(args.label, args.disk, exe, disk0, marks)


def save_record(label: str, disk: str, exe: str, disk0: int, marks: dict) -> dict:
    """Append one launch record to OUT and return it."""
    record = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "label": label,
        "disk": disk,
        "exe": exe,
        "uptime_h": round((time.time() - psutil.boot_time()) / 3600, 2),
        "steam_running": find_process("steam.exe") is not None,
        "system_read_mb": round((psutil.disk_io_counters().read_bytes - disk0) / 1e6, 1),
        "ram_used_pct": psutil.virtual_memory().percent,
        **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in marks.items()},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    print(json.dumps(record, indent=2))
    return record


if __name__ == "__main__":
    main()
