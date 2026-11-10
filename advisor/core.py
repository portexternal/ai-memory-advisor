"""Pure logic of the standalone AI Memory Advisor: config, learning phase, launch file sets,
target choice and the in-process prefetcher. No UI and no globals, so it is easy to test."""
import json
import os
import platform
import re
import sys
import time
import uuid
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path

import psutil

APP = "AIMemoryAdvisor"
VERSION = "0.2.0"
TERMS_VERSION = "1.0"           # docs/TERMS.md and docs/PRIVACY.md; a change makes the program ask again
GB = 1e9
CHUNK = 4 * 1024 * 1024
STOP_CHECK_BYTES = 512 * 1024 * 1024
STABLE_AFTER_LAUNCHES = 5      # from this many recorded launches on, rarely seen files are skipped
STABLE_MIN_SHARE = 0.2         # a file must appear in at least this share of launches

DEFAULTS = {
    "observe_days": 10,
    "poll_seconds": 1,
    "heartbeat_seconds": 300,
    "idle_threshold_seconds": 300,
    "status_seconds": 300,
    "tick_seconds": 600,
    "max_targets": 5,
    "min_set_mb": 200,
    "min_launches": 3,
    "heavy_set_gb": 2.0,
    "window_min": 30,
    "threshold": 0.3,
    "cooldown_min": 45,
    "min_free_gb": 3,
    "budget_fraction": 0.5,
    "budget_cap_fraction": 0.4,
    "max_cpu_percent": 60,
    "max_disk_mb_s": 50,
    "false_rate_alpha": 0.2,
    "false_rate_penalty": 0.3,
    "ignore_foreground": [],       # extra app names to ignore, on top of the built-in list per OS
}


def data_dir() -> Path:
    """Where logs, file sets and the report live. AIMA_DATA overrides it (tests, portable use)."""
    override = os.environ.get("AIMA_DATA")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / APP


def load_config(folder: Path) -> dict:
    """Defaults merged with config.json. The file is written on first run so it can be edited."""
    path = folder / "config.json"
    user = {}
    if path.exists():
        try:
            user = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            user = {}
    else:
        folder.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(DEFAULTS, indent=2), encoding="utf-8")
    return {**DEFAULTS, **user}


def recorded_days(log_dir: Path) -> int:
    """Days with a behaviour log. A day the PC was off does not count as a learning day."""
    if not log_dir.exists():
        return 0
    return sum(1 for p in log_dir.glob("*.jsonl") if p.stat().st_size > 0)


def phase(days: int, observe_days: int) -> str:
    """Learning for the first observe_days recorded days, active from the day after."""
    return "active" if days > observe_days else "learning"


def is_user_file(path: str, system_prefixes) -> bool:
    """True for a real file outside the operating system folders (those are shared and already
    cached). system_prefixes is one prefix or a tuple of them, compared case-insensitively."""
    if not path or not os.path.isabs(path):
        return False
    prefixes = (system_prefixes,) if isinstance(system_prefixes, str) else tuple(system_prefixes)
    if path.lower().startswith(tuple(p.lower() for p in prefixes)):
        return False
    return os.path.isfile(path)


def process_files(proc) -> list:
    """Open files, plus memory-mapped files where the OS lets a normal user list them (not macOS)."""
    paths = [f.path for f in proc.open_files()]
    maps = getattr(proc, "memory_maps", None)
    if maps:
        paths += [m.path for m in maps()]
    return paths


def snapshot_files(pid: int, system_prefixes) -> set:
    """Files a process and its children hold open or memory-mapped right now. No admin needed."""
    found = set()
    try:
        root = psutil.Process(pid)
        procs = [root] + root.children(recursive=True)
    except (psutil.Error, ValueError):
        return found
    for proc in procs:
        try:
            paths = process_files(proc)
        except (psutil.Error, OSError, NotImplementedError):
            continue
        found.update(p for p in paths if is_user_file(p, system_prefixes))
    return found


def fileset_path(folder: Path, exe: str) -> Path:
    return folder / "filesets" / (re.sub(r"[^a-z0-9._-]", "_", exe.lower()) + ".json")


def stable_files(data: dict) -> list:
    """[(path, size)] worth prefetching: most often seen first, then biggest first."""
    launches = max(1, data.get("launches", 1))
    seen = data.get("seen", {})
    out = []
    for path, ranges in data.get("files", {}).items():
        share = seen.get(path, 1) / launches
        if launches >= STABLE_AFTER_LAUNCHES and share < STABLE_MIN_SHARE:
            continue
        out.append((path, ranges[0][1], seen.get(path, 1)))
    out.sort(key=lambda row: (-row[2], -row[1]))
    return [(path, size) for path, size, _ in out]


def merge_fileset(fs_path: Path, exe: str, paths: set) -> dict:
    """Add one launch worth of files to the app's launch set. Returns the new set (a new dict)."""
    old = {"target": exe, "method": "open_files+memory_maps, whole files", "launches": 0, "seen": {}, "files": {}}
    if fs_path.exists():
        try:
            old = json.loads(fs_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    files, seen = dict(old.get("files", {})), dict(old.get("seen", {}))
    for path in paths:
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        if size > 0:
            files[path] = [[0, size]]
            seen[path] = seen.get(path, 0) + 1
    new = {**old, "files": files, "seen": seen, "launches": old.get("launches", 0) + 1}
    new["total_bytes"] = sum(size for _, size in stable_files(new))
    new["file_count"] = len(files)
    fs_path.parent.mkdir(parents=True, exist_ok=True)
    fs_path.write_text(json.dumps(new), encoding="utf-8")
    return new


def set_bytes(fs_path: Path) -> int:
    try:
        return int(json.loads(fs_path.read_text(encoding="utf-8")).get("total_bytes", 0))
    except (ValueError, OSError):
        return 0


def launch_counts(evs: list) -> Counter:
    return Counter(e["app"] for e in evs if e.get("type") == "app_start" and e.get("app"))


def pick_targets(cfg: dict, folder: Path, evs: list) -> list:
    """Apps worth preparing: launched often enough and with a big enough launch set.
    Ranked by launches x set size, so the apps where a prefetch pays most come first."""
    ranked = []
    for exe, count in launch_counts(evs).items():
        size = set_bytes(fileset_path(folder, exe))
        if count >= cfg["min_launches"] and size >= cfg["min_set_mb"] * 1e6:
            ranked.append((count * size, exe, size, count))
    ranked.sort(reverse=True)
    return [{"exe": exe, "fileset": str(fileset_path(folder, exe)), "enabled": True,
             "set_gb": round(size / GB, 2), "launches": count}
            for _, exe, size, count in ranked[:cfg["max_targets"]]]


def build_multi_cfg(cfg: dict, targets: list) -> dict:
    """Config in the shape predictor/multi.py expects. Budget cap follows installed RAM."""
    total_gb = psutil.virtual_memory().total / GB
    return {
        "targets": targets,
        "window_min": cfg["window_min"], "threshold": cfg["threshold"], "cooldown_min": cfg["cooldown_min"],
        "min_free_gb": cfg["min_free_gb"], "budget_fraction": cfg["budget_fraction"],
        "budget_cap_gb": round(total_gb * cfg["budget_cap_fraction"], 1),
        "heavy_apps": [t["exe"] for t in targets if t["set_gb"] >= cfg["heavy_set_gb"]],
        "max_cpu_percent": cfg["max_cpu_percent"], "max_disk_mb_s": cfg["max_disk_mb_s"],
        "false_rate_alpha": cfg["false_rate_alpha"], "false_rate_penalty": cfg["false_rate_penalty"],
        "tick_seconds": cfg["tick_seconds"],
    }


SHARE_FIELDS = ("app", "launch_s", "prefetched", "p", "budget_gb", "read_gb", "seconds", "lead_min",
                "reason", "ram_used_pct", "idle_seconds")


def share_id(folder: Path) -> str:
    """Random id for this install, so two exports from one PC can be joined. Not derived from anything."""
    path = folder / "share_id.txt"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    value = uuid.uuid4().hex
    path.write_text(value, encoding="utf-8")
    return value


def anonymize_events(evs: list) -> list:
    """Keep what a predictor needs (weekday, time of day, app, outcome). Drop dates, pids, epochs."""
    out, first_day = [], None
    for ev in evs:
        try:
            t = datetime.fromisoformat(ev["ts"]).replace(tzinfo=None)
        except (KeyError, ValueError):
            continue
        first_day = first_day or t.date()
        row = {"day": (t.date() - first_day).days, "wd": t.weekday(), "t": t.strftime("%H:%M:%S"), "type": ev.get("type")}
        row.update({k: ev[k] for k in SHARE_FIELDS if ev.get(k) is not None})
        out.append(row)
    return out


def fileset_summaries(folder: Path) -> list:
    """Sizes and counts only. File paths never leave the PC: they can hold user names."""
    rows = []
    for path in sorted((folder / "filesets").glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        rows.append({"app": data.get("target"), "launches": data.get("launches", 0),
                     "file_count": data.get("file_count", 0), "total_bytes": data.get("total_bytes", 0)})
    return rows


def export_share(folder: Path, evs: list) -> Path:
    """Write the anonymised data set to <folder>/share/*.zip. Nothing is sent anywhere."""
    vm = psutil.virtual_memory()
    payload = {
        "format": 1, "version": VERSION, "install_id": share_id(folder),
        "consent": {"sharing_terms_version": TERMS_VERSION, "agreed_in_app": True},
        "machine": {"ram_gb": round(vm.total / GB), "cpu_threads": psutil.cpu_count(), "os": platform.release()},
        "recorded_days": recorded_days(folder / "logs"),
        "filesets": fileset_summaries(folder),
        "events": anonymize_events(evs),
    }
    out = folder / "share" / f"aima-share-{payload['install_id'][:8]}-{datetime.now():%Y%m%d}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("share.json", json.dumps(payload, ensure_ascii=False))
    return out


def read_file(path: str, size: int, budget_left: int) -> int:
    """Buffered read so the Windows cache keeps the pages. Returns bytes read."""
    done = 0
    try:
        with open(path, "rb", buffering=0) as fh:
            remaining = min(size, budget_left)
            while remaining > 0:
                data = fh.read(min(CHUNK, remaining))
                if not data:
                    break
                remaining -= len(data)
                done += len(data)
    except OSError:
        pass
    return done


def prefetch_fileset(fs_path: Path, budget_gb: float, should_stop=lambda: False) -> dict:
    """Read an app's launch set into the file cache, up to budget_gb. should_stop is asked
    every 512 MB so a heavy app that starts meanwhile wins at once."""
    try:
        data = json.loads(fs_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return {"error": str(exc)[:200]}
    budget, total, since_check, stopped = int(budget_gb * GB), 0, 0, False
    proc, t0 = psutil.Process(), time.perf_counter()
    cpu0 = proc.cpu_times()
    for path, size in stable_files(data):
        if total >= budget:
            break
        got = read_file(path, size, budget - total)
        total += got
        since_check += got
        if since_check >= STOP_CHECK_BYTES:
            since_check = 0
            if should_stop():
                stopped = True
                break
    cpu1 = proc.cpu_times()
    return {"read_gb": round(total / GB, 2), "seconds": round(time.perf_counter() - t0, 1), "stopped": stopped,
            "cpu_seconds": round((cpu1.user - cpu0.user) + (cpu1.system - cpu0.system), 2)}
