"""Multi-app cold / prefetched / warm launch loop, no admin (2026-11-11, professor asked for more data than CS2).

Per app and round:
  cold        evict the file cache (RAM pressure to ~1 GB free, verified to drop a cached file to disk speed), launch
  prefetched  evict, buffered-read the app's prefetch set (install dir, capped at 12.4 GB, CS2 = recorded file set), launch
  warm        launch again right after a launch (ceiling)
Launch = start the exe (or steam://rungameid), wait for the process, record t0 -> first visible window -> disk reads settled
with bench/launch_timer.track(require_f9=False), then kill. One JSON row per launch in data/bench/launches.jsonl
(label <app>-<cond>, disk nvme|hdd). Progress in data/bench/app_loop.log.

Usage: python bench/run_app_loop.py --apps blender,cursor,chrome,photoshop,cs2,wukong,wardogs --rounds 4
"""
import argparse
import gc
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bench.launch_timer import find_process, save_record, track, wait_for_process  # noqa: E402

LOG = ROOT / "data" / "bench" / "app_loop.log"
CAP_GB = 12.4
CHUNK = 8 * 1024 * 1024
STEAM = r"C:\Program Files (x86)\Steam\steamapps\common"
LOCAL = os.environ.get("LOCALAPPDATA", "")

APPS = {
    "blender": {"exe": "blender.exe", "start": r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
                "disk": "nvme", "prefetch_dirs": [r"C:\Program Files\Blender Foundation\Blender 5.2"], "kill": ["blender.exe"]},
    "blender-hdd": {"exe": "blender.exe", "start": r"D:\llbench\Blender52\blender.exe", "disk": "hdd",
                    "prefetch_dirs": [r"D:\llbench\Blender52"], "kill": ["blender.exe"]},
    "blender-cloud": {"exe": "blender.exe", "start": r"C:\Users\user\OneDrive\llbench\Blender52\blender.exe", "disk": "cloud",
                      "prefetch_dirs": [r"C:\Users\user\OneDrive\llbench\Blender52"], "kill": ["blender.exe"],
                      "dehydrate": r"C:\Users\user\OneDrive\llbench\Blender52"},
    "photoshop": {"exe": "photoshop.exe", "start": r"C:\Program Files\Adobe\Adobe Photoshop 2025\Photoshop.exe",
                  "disk": "nvme", "prefetch_dirs": [r"C:\Program Files\Adobe\Adobe Photoshop 2025", r"C:\Program Files\Adobe\Common"],
                  "kill": ["Photoshop.exe", "CEPHtmlEngine.exe", "Adobe Crash Processor.exe"]},
    "cursor": {"exe": "cursor.exe", "start": LOCAL + r"\Programs\cursor\Cursor.exe", "disk": "nvme",
               "prefetch_dirs": [LOCAL + r"\Programs\cursor"], "kill": ["Cursor.exe"]},
    "chrome": {"exe": "chrome.exe", "start": r"C:\Program Files\Google\Chrome\Application\chrome.exe", "disk": "nvme",
               "prefetch_dirs": [r"C:\Program Files\Google\Chrome\Application"], "kill": ["chrome.exe"], "skip_if_running": True},
    "cs2": {"exe": "cs2.exe", "steam": "730", "disk": "nvme", "fileset": ROOT / "data" / "fileset" / "cs2_launch.json",
            "kill": ["cs2.exe"]},
    "wukong": {"exe": "b1-win64-shipping.exe", "steam": "2358720", "disk": "nvme",
               "prefetch_dirs": [STEAM + r"\BlackMythWukong"], "prefetch_ext": (".exe", ".dll", ".pak", ".utoc"),
               "prefetch_names": ("pakchunk0-windows.ucas",), "kill": ["b1-Win64-Shipping.exe", "b1.exe"]},
    "wardogs": {"exe": "wardogsclient-win64-shipping.exe", "steam": "1867240", "disk": "hdd",
                "prefetch_dirs": [r"D:\SteamLibrary\steamapps\common\Wardogs"], "prefetch_ext": (".exe", ".dll", ".pak", ".utoc"),
                "prefetch_names": ("pakchunk0-windowsclient.ucas",),
                "kill": ["WardogsClient-Win64-Shipping.exe", "WardogsLauncher-Shipping.exe", "EpicWebHelper.exe"]},
}


def log(msg: str):
    line = f"{datetime.now().strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def evict() -> float:
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
    return round(held, 1)


def prefetch_files(app: dict) -> list:
    if app.get("fileset"):
        fs = json.loads(Path(app["fileset"]).read_text(encoding="utf-8"))
        return [Path(p) for p in fs["files"]]
    ext = app.get("prefetch_ext")
    names = tuple(n.lower() for n in app.get("prefetch_names", ()))
    out = []
    for d in app["prefetch_dirs"]:
        for p in Path(d).rglob("*"):
            if not p.is_file():
                continue
            n = p.name.lower()
            if ext is None or n.endswith(ext) or n in names:
                out.append(p)
    return out


def prefetch(files: list) -> dict:
    budget, total, t0 = int(CAP_GB * 1e9), 0, time.perf_counter()
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
    return {"prefetch_gb": round(total / 1e9, 2), "prefetch_s": round(time.perf_counter() - t0, 1), "prefetch_files": len(files)}


def kill(app: dict):
    for name in app["kill"]:
        subprocess.run(["taskkill", "/IM", name, "/F"], capture_output=True)
    time.sleep(4)


def launch_once(name: str, app: dict, label: str, settle: float, extra: dict) -> dict:
    exe = app["exe"]
    if find_process(exe):
        kill(app)
    disk0 = psutil.disk_io_counters().read_bytes
    if app.get("steam"):
        subprocess.run(["cmd", "/c", "start", "", f"steam://rungameid/{app['steam']}"], capture_output=True)
    else:
        subprocess.Popen([app["start"]], cwd=str(Path(app["start"]).parent),
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    proc = wait_for_process(exe, 180)
    if not proc:
        log(f"  {label}: no process in 180 s")
        return {}
    marks = track(proc, exe, settle, require_f9=False)
    rec = save_record(label, app["disk"], exe, disk0, {**marks, **extra, "app": name})
    kill(app)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apps", default="blender,cursor,chrome,photoshop,cs2,wukong,wardogs")
    ap.add_argument("--rounds", type=int, default=4)
    ap.add_argument("--settle", type=float, default=60)
    ap.add_argument("--conditions", default="cold,prefetched,warm")
    a = ap.parse_args()
    conds = a.conditions.split(",")
    for name in a.apps.split(","):
        app = APPS[name]
        if app.get("skip_if_running") and find_process(app["exe"]):
            log(f"{name}: already running (someone is using it), skipped")
            continue
        files = prefetch_files(app) if "prefetched" in conds else []
        size = sum(p.stat().st_size for p in files if p.exists()) / 1e9 if files else 0
        log(f"== {name} on {app['disk']}: prefetch set {len(files)} files {size:.1f} GB (cap {CAP_GB})")
        for r in range(1, a.rounds + 1):
            for c in conds:
                extra = {"round": r}
                try:
                    if c in ("cold", "prefetched"):
                        if app.get("dehydrate"):   # OneDrive Files On Demand: make the copy online-only again
                            subprocess.run(["attrib", "+U", "-P", app["dehydrate"] + r"\*", "/S", "/D"], capture_output=True)
                            time.sleep(5)
                        extra["evicted_gb"] = evict()
                    if c == "prefetched":
                        extra.update(prefetch(files))
                    rec = launch_once(name, app, f"{name}-{c}", a.settle, extra)
                    if rec:
                        log(f"  {name} r{r} {c}: window {rec.get('window')} s, settled {rec.get('io_settled')} s, read {rec.get('system_read_mb')} MB")
                except Exception as exc:  # keep the loop alive for the next app
                    log(f"  {name} r{r} {c}: ERROR {str(exc)[:160]}")
                    kill(app)
                time.sleep(8)
    log("== loop done")


if __name__ == "__main__":
    main()
