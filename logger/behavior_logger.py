"""Behavior logger daemon. Polls foreground app, watched process starts/stops,
idle state, heartbeat and weather. Writes JSONL to data/logs and rebuilds the
HTML dashboard periodically. Run with pythonw.exe for a silent background task."""
import json
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psutil  # noqa: E402

from logger import events, weather, winapi  # noqa: E402

LOCK_FILE = ROOT / "data" / "logger.lock"
ERR_FILE = ROOT / "data" / "logger.err"


def load_config() -> dict:
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def acquire_single_instance() -> bool:
    """Refuse to start if another logger pid is alive."""
    if LOCK_FILE.exists():
        try:
            old_pid = int(LOCK_FILE.read_text().strip())
            if psutil.pid_exists(old_pid) and old_pid != os.getpid():
                return False
        except ValueError:
            pass
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(str(os.getpid()))
    return True


def record_error():
    ERR_FILE.parent.mkdir(parents=True, exist_ok=True)
    with ERR_FILE.open("a", encoding="utf-8") as fh:
        fh.write(traceback.format_exc())


def rebuild_dashboard():
    from dashboard import build_dashboard
    try:
        build_dashboard.build()
    except Exception:  # dashboard failure must never kill the logger
        record_error()


def log_weather():
    try:
        events.write("weather", **weather.snapshot())
    except Exception as exc:  # offline is normal: record it and move on
        events.write("weather_error", error=str(exc)[:200])


class State:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.running = {}
        self.first_pass = True
        self.foreground = None
        self.is_idle = False
        now = time.time()
        self.next_heartbeat = now
        self.next_weather = now
        self.next_dashboard = now + cfg["dashboard_rebuild_seconds"]


def diff_processes(state: State):
    """Open apps = every process with a visible window + the always-watch list
    (catches background apps such as steam.exe with no window)."""
    current = winapi.windowed_process_names(state.cfg["ignore_foreground"])
    current.update(winapi.running_process_names(state.cfg["watch_apps"]))
    # first pass after logger start: apps already open are "seen", not launched
    kind = "app_seen" if state.first_pass else "app_start"
    for name, pid in current.items():
        if name not in state.running:
            try:
                exe = psutil.Process(pid).exe()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                exe = ""
            events.write(kind, app=name, pid=pid, exe=exe)
    state.first_pass = False
    for name in list(state.running):
        if name not in current:
            events.write("app_stop", app=name)
    state.running = current


def track_foreground(state: State):
    fg = winapi.foreground_process()
    if not fg:
        return
    pid, name, title = fg
    if name in state.cfg["ignore_foreground"] or name == state.foreground:
        return
    fields = {"app": name, "pid": pid}
    if state.cfg["log_window_titles"]:
        fields["title"] = title[:120]
    events.write("focus", **fields)
    state.foreground = name


def track_idle(state: State):
    idle = winapi.idle_seconds()
    threshold = state.cfg["idle_threshold_seconds"]
    if idle >= threshold and not state.is_idle:
        events.write("idle_start", idle_seconds=int(idle))
        state.is_idle = True
    elif idle < threshold and state.is_idle:
        events.write("idle_end")
        state.is_idle = False


def timers(state: State):
    now = time.time()
    if now >= state.next_heartbeat:
        vm = psutil.virtual_memory()
        events.write("heartbeat", ram_used_pct=vm.percent, ram_avail_mb=vm.available // (1024 * 1024))
        state.next_heartbeat = now + state.cfg["heartbeat_seconds"]
    if now >= state.next_weather:
        log_weather()
        state.next_weather = now + state.cfg["weather_seconds"]
    if now >= state.next_dashboard:
        rebuild_dashboard()
        state.next_dashboard = now + state.cfg["dashboard_rebuild_seconds"]


def main():
    if not acquire_single_instance():
        return
    cfg = load_config()
    state = State(cfg)
    events.write("session_start", boot_epoch=int(psutil.boot_time()), logger_pid=os.getpid())
    rebuild_dashboard()
    try:
        while True:
            diff_processes(state)
            track_foreground(state)
            track_idle(state)
            timers(state)
            time.sleep(cfg["poll_seconds"])
    except KeyboardInterrupt:
        pass
    finally:
        events.write("session_end")
        rebuild_dashboard()
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        record_error()
        raise
