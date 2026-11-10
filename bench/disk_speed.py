"""Storage read-speed probe for the slow-storage experiment (2026-11-11).

For a file on any volume (NVMe, HDD, USB, network share, OneDrive) measure:
  uncached  = read with FILE_FLAG_NO_BUFFERING, bypasses the Windows file cache  -> what a COLD launch sees
  cached    = ordinary buffered read right after                                   -> what a PREFETCHED launch sees
Then scale both to the CS2 launch set (13.5 GB read) to estimate the disk-bound part of a launch on that medium.

Usage:
  python bench/disk_speed.py make  <dir> [--gb 2]        # create <dir>/llspeed_<gb>gb.bin filled with pseudo-random bytes
  python bench/disk_speed.py test  <file> [--label hdd]  # measure uncached then cached read, append a row to data/bench/disk_speed.jsonl
No admin needed. NO_BUFFERING needs 4 KiB-aligned buffers, handled here with ctypes.
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "bench" / "disk_speed.jsonl"
CHUNK = 8 * 1024 * 1024          # 8 MiB per ReadFile
SET_GB = 13.5                    # CS2 launch read on this PC (bench 2026-10-05)
OVERLAP = 0.62                   # share of read time visible in the launch, from the NVMe measurement

GENERIC_READ = 0x80000000
FILE_SHARE_READ = 1
OPEN_EXISTING = 3
FILE_FLAG_NO_BUFFERING = 0x20000000
FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000
INVALID = ctypes.c_void_p(-1).value

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.restype = wt.HANDLE
k32.ReadFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
k32.VirtualAlloc.restype = ctypes.c_void_p
k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wt.DWORD, wt.DWORD]


def make(dir_: str, gb: float) -> Path:
    path = Path(dir_) / f"llspeed_{gb:g}gb.bin"
    if path.exists() and path.stat().st_size >= gb * 1e9:
        print("exists", path)
        return path
    total = int(gb * 1e9)
    block = os.urandom(4 * 1024 * 1024)          # random so no compression or dedupe can cheat
    written = 0
    t0 = time.perf_counter()
    with open(path, "wb") as fh:
        while written < total:
            fh.write(block)
            written += len(block)
    print(f"wrote {written / 1e9:.2f} GB to {path} in {time.perf_counter() - t0:.1f} s")
    return path


def read_uncached(path: str) -> tuple:
    h = k32.CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, None, OPEN_EXISTING,
                        FILE_FLAG_NO_BUFFERING | FILE_FLAG_SEQUENTIAL_SCAN, None)
    if h == INVALID or h is None:
        raise OSError(ctypes.get_last_error(), "CreateFile failed")
    buf = k32.VirtualAlloc(None, CHUNK, 0x3000, 0x04)   # MEM_COMMIT|MEM_RESERVE, PAGE_READWRITE: page aligned
    got = wt.DWORD(0)
    total = 0
    t0 = time.perf_counter()
    while True:
        ok = k32.ReadFile(h, buf, CHUNK, ctypes.byref(got), None)
        if not ok or got.value == 0:
            break
        total += got.value
    k32.CloseHandle(h)
    return total, time.perf_counter() - t0


def read_cached(path: str) -> tuple:
    total = 0
    t0 = time.perf_counter()
    with open(path, "rb", buffering=0) as fh:
        while True:
            b = fh.read(CHUNK)
            if not b:
                break
            total += len(b)
    return total, time.perf_counter() - t0


def test(path: str, label: str, warm_runs: int = 2) -> dict:
    size, t_un = read_uncached(path)
    _, t_c1 = read_cached(path)                    # first buffered read: pulls into cache (= the prefetch itself)
    cached = [read_cached(path)[1] for _ in range(warm_runs)]
    t_c = min(cached)
    mbs_un, mbs_c = size / 1e6 / t_un, size / 1e6 / t_c
    set_read_cold = SET_GB * 1e9 / (size / t_un)
    set_read_cached = SET_GB * 1e9 / (size / t_c)
    row = {
        "ts": datetime.now().isoformat(timespec="seconds"), "label": label, "path": path, "bytes": size,
        "uncached_s": round(t_un, 2), "uncached_mb_s": round(mbs_un, 1),
        "first_buffered_s": round(t_c1, 2), "cached_s": round(t_c, 2), "cached_mb_s": round(mbs_c, 1),
        "est_set_read_cold_s": round(set_read_cold, 1), "est_set_read_cached_s": round(set_read_cached, 1),
        "est_launch_disk_part_cold_s": round(OVERLAP * set_read_cold, 1),
        "est_cold_launch_s": round(14.6 + OVERLAP * set_read_cold, 1),
        "est_prefetched_launch_s": 14.6,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    print(json.dumps(row, indent=1))
    return row


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make"); m.add_argument("dir"); m.add_argument("--gb", type=float, default=2.0)
    t = sub.add_parser("test"); t.add_argument("file"); t.add_argument("--label", default="unlabeled")
    a = ap.parse_args()
    if a.cmd == "make":
        make(a.dir, a.gb)
    else:
        test(a.file, a.label)


if __name__ == "__main__":
    main()
