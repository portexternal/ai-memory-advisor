"""Cold / prefetched / warm launch loop for a game on the spinning HDD, no admin needed (2026-11-11).

Cache eviction without RAMMap: allocate RAM until ~0.8 GB is left so Windows drops the standby list, then free it.
Verified on this PC: a 2 GB cached file fell from 3.3 GB/s to ~0.3 GB/s after the pressure.

Conditions per round:
  cold        evict, then launch
  prefetched  evict, buffered-read the prefetch set (binaries + pakchunk0 + every .pak/.utoc), then launch
  warm        launch again right after the previous one (ceiling)
Each launch: start via steam://rungameid/<id>, wait for the shipping exe, record t0 -> first window with
bench/launch_timer.track(), then kill the game. One JSON row per launch in data/bench/launches.jsonl
(label hdd-cold / hdd-prefetched / hdd-warm, disk hdd).

Usage: python bench/run_hdd_loop.py --rounds 5 [--settle 45] [--conditions cold,prefetched,warm]
"""
import argparse
import gc
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bench.launch_timer import find_process, save_record, track, wait_for_process  # noqa: E402

APP_ID = "1867240"
EXE = "wardogsclient-win64-shipping.exe"
KILL = ["WardogsClient-Win64-Shipping.exe", "WardogsLauncher-Shipping.exe", "EpicWebHelper.exe"]
GAME = Path(r"D:\SteamLibrary\steamapps\common\Wardogs")
PREFETCH_SET_GB_CAP = 12.4
CHUNK = 8 * 1024 * 1024


def evict() -> float:
    """RAM pressure until ~0.8 GB available, then release. Returns GB held."""
    blocks, step = [], 512 * 1024 * 1024
    try:
        while psutil.virtual_memory().available > 1.0e9:
            b = bytearray(step)
            b[::4096] = b"\x01" * (step // 4096)
            blocks.append(b)
    except MemoryError:
        pass
    held = len(blocks) * step / 1e9
    time.sleep(3)
    del blocks
    gc.collect()
    time.sleep(3)
    return held


def prefetch_set() -> list:
    files = []
    for p in GAME.rglob("*"):
        if not p.is_file():
            continue
        n = p.name.lower()
        if n.endswith((".exe", ".dll", ".pak", ".utoc")) or n == "pakchunk0-windowsclient.ucas":
            files.append(p)
    return files


def prefetch(files: list) -> dict:
    budget = int(PREFETCH_SET_GB_CAP * 1e9)
    total, t0 = 0, time.perf_counter()
    for p in files:
        if total >= budget:
            break
        try:
            with open(p, "rb", buffering=0) as fh:
                while True:
                    b = fh.read(CHUNK)
                    if not b:
                        break
                    total += len(b)
        except OSError:
            continue
    return {"prefetch_gb": round(total / 1e9, 2), "prefetch_s": round(time.perf_counter() - t0, 1)}


def kill_game():
    for name in KILL:
        subprocess.run(["taskkill", "/IM", name, "/F"], capture_output=True)
    time.sleep(5)


def launch_once(label: str, settle: float, extra: dict) -> dict:
    if find_process(EXE):
        kill_game()
    disk0 = psutil.disk_io_counters().read_bytes
    subprocess.run(["cmd", "/c", "start", "", f"steam://rungameid/{APP_ID}"], capture_output=True)
    proc = wait_for_process(EXE, 180)
    if not proc:
        print(f"  {label}: no launch seen in 180 s", flush=True)
        return {}
    marks = track(proc, EXE, settle)
    rec = save_record(label, "hdd", EXE, disk0, {**marks, **extra})
    kill_game()
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--settle", type=float, default=45)
    ap.add_argument("--conditions", default="cold,prefetched,warm")
    a = ap.parse_args()
    conds = a.conditions.split(",")
    files = prefetch_set()
    print(f"prefetch set: {len(files)} files, {sum(p.stat().st_size for p in files) / 1e9:.1f} GB (cap {PREFETCH_SET_GB_CAP})", flush=True)
    for r in range(1, a.rounds + 1):
        for c in conds:
            extra = {"round": r}
            if c in ("cold", "prefetched"):
                extra["evicted_gb"] = round(evict(), 1)
            if c == "prefetched":
                extra.update(prefetch(files))
            print(f"round {r} {c}: launching", flush=True)
            rec = launch_once(f"hdd-{c}", a.settle, extra)
            if rec:
                print(f"  window {rec.get('window')} s, read {rec.get('system_read_mb')} MB", flush=True)
            time.sleep(10)


if __name__ == "__main__":
    main()
