"""AI Memory Advisor, standalone program (build with advisor/build_exe.ps1).

Days 1 to observe_days: only watches which apps are opened and when, and records the files each
app needs to start. From the next recorded day on: predicts the next heavy app and reads its
launch files into the Windows file cache before the user opens it.

  AIMemoryAdvisor.exe            first run asks for consent, then runs in the background;
                                 run again to open the report or to stop it
  AIMemoryAdvisor.exe --stop     stop the running copy and remove the autostart entry
  AIMemoryAdvisor.exe --once     write the report and the current plan once, read nothing, exit
  AIMemoryAdvisor.exe --share    opt-in: write an anonymised copy of the data to a zip (never uploads)

Everything stays in %LOCALAPPDATA%\\AIMemoryAdvisor. No network, no window titles."""
import ctypes
import json
import os
import sys
import threading
import time
import traceback
import webbrowser
import winreg
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psutil  # noqa: E402

from advisor import core, status  # noqa: E402
from logger import events, winapi  # noqa: E402
from predictor import multi  # noqa: E402

TITLE = "AI Memory Advisor"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
SNAP_DELAYS = (5, 20, 45)          # seconds after a launch at which the open files are recorded
MAX_LAUNCH_S = 300
MB_YESNO, MB_YESNOCANCEL, MB_INFO, ID_YES, ID_NO = 4, 3, 0x40, 6, 7
TEST_MODE = bool(os.environ.get("AIMA_TEST"))
SHARE_URL = "https://github.com/portexternal/ai-memory-advisor/blob/main/docs/SHARING.md"

CONSENT = (
    "This program learns which apps you open and when, for {days} days. From day {next} it pre-loads the "
    "files of the app it expects you to open next, so that app starts faster.\n\n"
    "Recorded: program names (for example blender.exe), start and stop times, idle periods, free memory.\n"
    "NOT recorded: window titles, keystrokes, screenshots, file contents.\n"
    "Nothing is sent over the network. Everything stays in:\n{folder}\n\n"
    "It starts by itself when you sign in to Windows. To see the report or to stop it, run this file again.\n\n"
    "Start now?")


def msgbox(text: str, flags: int) -> int:
    return ctypes.windll.user32.MessageBoxW(0, text, TITLE, flags | MB_INFO)


def log_error(folder: Path):
    """Keep the last errors for debugging. Never let the log grow without bound."""
    path = folder / "error.log"
    old = path.read_text(encoding="utf-8")[-20000:] if path.exists() else ""
    path.write_text(old + f"\n{datetime.now().isoformat(timespec='seconds')}\n{traceback.format_exc()}", encoding="utf-8")


def set_autostart(on: bool):
    """Per-user Run entry. Only for the built exe, never from source or in test mode."""
    if not getattr(sys, "frozen", False) or TEST_MODE:
        return
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, core.APP, 0, winreg.REG_SZ, f'"{sys.executable}"')
        else:
            try:
                winreg.DeleteValue(key, core.APP)
            except FileNotFoundError:
                pass


def other_instance(lock: Path) -> int:
    """Pid of a running copy, or 0."""
    try:
        pid = int(lock.read_text().strip())
    except (OSError, ValueError):
        return 0
    return pid if pid != os.getpid() and psutil.pid_exists(pid) else 0


def stop_instance(folder: Path, pid: int):
    if pid:
        try:
            psutil.Process(pid).terminate()
        except psutil.Error:
            pass
    set_autostart(False)
    (folder / "advisor.lock").unlink(missing_ok=True)


def consent(folder: Path, cfg: dict) -> bool:
    """Ask once. The answer is remembered so the logon start is silent."""
    marker = folder / "consent.json"
    if marker.exists() or TEST_MODE:
        return True
    text = CONSENT.format(days=cfg["observe_days"], next=cfg["observe_days"] + 1, folder=folder)
    if msgbox(text, MB_YESNO) != ID_YES:
        return False
    marker.write_text(json.dumps({"accepted": datetime.now().isoformat(timespec="seconds"), "version": core.VERSION}))
    set_autostart(True)
    return True


SHARE_ASK = (
    "Create a file with your anonymised usage data, to help improve the predictor?\n\n"
    "In the file: program names, weekday and time of day of each start and stop, launch times, "
    "prediction results, memory size, and the size of each app's launch file set.\n"
    "NOT in the file: dates, file names or paths, user name, window titles.\n\n"
    "Nothing is sent. The file is only written to your disk and you decide whether to send it.")


def share(folder: Path):
    """Opt-in export. Explains first, writes a zip, shows where it is. Never uploads."""
    if not TEST_MODE and msgbox(SHARE_ASK, MB_YESNO) != ID_YES:
        return
    out = core.export_share(folder, events.read_all())
    if not TEST_MODE:
        msgbox(f"Created:\n{out}\n\nYou can open it and read it first (share.json inside).\n"
               f"Where to send it: {SHARE_URL}", 0)
        os.startfile(out.parent)


def second_launch(folder: Path, cfg: dict, pid: int):
    days = core.recorded_days(folder / "logs")
    state = "active" if core.phase(days, cfg["observe_days"]) == "active" else f"learning, day {days} of {cfg['observe_days']}"
    answer = msgbox(f"The advisor is running ({state}).\n\nYes = open the report\nNo = stop it and remove it from startup\n"
                    "Cancel = leave it running", MB_YESNOCANCEL)
    if answer == ID_YES:
        webbrowser.open(status.render(folder, cfg, events.read_all()).as_uri())
    elif answer == ID_NO:
        stop_instance(folder, pid)
        msgbox(f"Stopped. The recorded data is still in:\n{folder}\nDelete that folder to remove everything.", 0)


class Shared:
    """State shared between the logger loop and the predictor thread."""

    def __init__(self):
        self._fired = {}
        self._lock = threading.Lock()

    def mark_fired(self, exe: str):
        with self._lock:
            self._fired = {**self._fired, exe: datetime.now()}

    def recently_fired(self, exe: str, window_min: int) -> bool:
        fired = self._fired.get(exe)
        return bool(fired and datetime.now() - fired <= timedelta(minutes=window_min))


class Tracker:
    """Logger loop state: open apps, foreground app, idle, pending file snapshots, timers."""

    def __init__(self, folder: Path, cfg: dict, shared: Shared):
        self.folder, self.cfg, self.shared = folder, cfg, shared
        self.running, self.pending = {}, {}
        self.first_pass, self.foreground, self.is_idle = True, None, False
        self.next_heartbeat = time.time()
        self.next_status = time.time()
        self.windir = os.environ.get("WINDIR", r"C:\Windows")

    def note_start(self, name: str, pid: int):
        """A new app window appeared: log it, time it, and schedule file snapshots."""
        try:
            proc = psutil.Process(pid)
            launch_s, exe = round(time.time() - proc.create_time(), 1), proc.exe()
        except psutil.Error:
            launch_s, exe = None, ""
        if launch_s is not None and not 0 <= launch_s <= MAX_LAUNCH_S:
            launch_s = None
        events.write("app_start", app=name, pid=pid, launch_s=launch_s,
                     prefetched=self.shared.recently_fired(name, self.cfg["window_min"]))
        if core.is_user_file(exe, self.windir):
            now = time.time()
            self.pending[(name, pid)] = {"due": [now + s for s in SNAP_DELAYS], "paths": set()}

    def diff_processes(self):
        current = winapi.windowed_process_names(self.cfg["ignore_foreground"])
        for name, pid in current.items():
            if name in self.running:
                continue
            if self.first_pass:
                events.write("app_seen", app=name, pid=pid)   # already open, not a launch
            else:
                self.note_start(name, pid)
        self.first_pass = False
        for name in self.running:
            if name not in current:
                events.write("app_stop", app=name)
        self.running = current

    def take_snapshots(self):
        """Record open files a few times during a launch, then merge them into the app's set."""
        now = time.time()
        for key in list(self.pending):
            job = self.pending[key]
            if now < job["due"][0]:
                continue
            job["due"].pop(0)
            job["paths"] |= core.snapshot_files(key[1])
            if not job["due"]:
                del self.pending[key]
                if job["paths"]:
                    core.merge_fileset(core.fileset_path(self.folder, key[0]), key[0], job["paths"])

    def track_focus_and_idle(self):
        fg = winapi.foreground_process()
        if fg and fg[1] not in self.cfg["ignore_foreground"] and fg[1] != self.foreground:
            events.write("focus", app=fg[1], pid=fg[0])
            self.foreground = fg[1]
        idle, limit = winapi.idle_seconds(), self.cfg["idle_threshold_seconds"]
        if idle >= limit and not self.is_idle:
            events.write("idle_start", idle_seconds=int(idle))
            self.is_idle = True
        elif idle < limit and self.is_idle:
            events.write("idle_end")
            self.is_idle = False

    def timers(self):
        now = time.time()
        if now >= self.next_heartbeat:
            vm = psutil.virtual_memory()
            events.write("heartbeat", ram_used_pct=vm.percent, ram_avail_mb=vm.available // (1024 * 1024))
            self.next_heartbeat = now + self.cfg["heartbeat_seconds"]
        if now >= self.next_status:
            status.render(self.folder, self.cfg, events.read_all())
            self.next_status = now + self.cfg["status_seconds"]

    def step(self):
        self.diff_processes()
        self.take_snapshots()
        self.track_focus_and_idle()
        self.timers()


def heavy_running(mcfg: dict, target: str) -> bool:
    """True when another heavy app is running: present performance wins over a prediction."""
    heavy = set(mcfg["heavy_apps"]) - {target}
    return bool(heavy & multi.running_set())


def predictor_tick(folder: Path, cfg: dict, shared: Shared, act: bool) -> dict:
    """One prediction round. Does nothing during the learning days."""
    days = core.recorded_days(folder / "logs")
    if core.phase(days, cfg["observe_days"]) == "learning":
        return {"phase": "learning", "day": days}
    evs = events.read_all()
    targets = core.pick_targets(cfg, folder, evs)
    if not targets:
        return {"phase": "active", "day": days, "plan": [], "note": "no heavy app learned yet"}
    mcfg = core.build_multi_cfg(cfg, targets)

    def run_prefetch(target: dict, budget_gb: float, _label: str) -> dict:
        shared.mark_fired(target["exe"])
        return core.prefetch_fileset(Path(target["fileset"]), budget_gb, lambda: heavy_running(mcfg, target["exe"]))

    multi.run_prefetch = run_prefetch
    report = {"phase": "active", "day": days, **multi.tick(mcfg, multi.load_state(), act=act)}
    (folder / "last_plan.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report


def predictor_loop(folder: Path, cfg: dict, shared: Shared):
    announced = False
    while True:
        time.sleep(cfg["tick_seconds"])
        try:
            report = predictor_tick(folder, cfg, shared, act=True)
            if report["phase"] == "active" and not announced:
                events.write("advisor_active", day=report["day"])
                announced = True
        except Exception:   # keep the thread alive, record the failure
            log_error(folder)


def run(folder: Path, cfg: dict):
    lock = folder / "advisor.lock"
    lock.write_text(str(os.getpid()))
    shared = Shared()
    events.write("session_start", boot_epoch=int(psutil.boot_time()), logger_pid=os.getpid(), version=core.VERSION)
    threading.Thread(target=predictor_loop, args=(folder, cfg, shared), daemon=True).start()
    tracker = Tracker(folder, cfg, shared)
    try:
        while True:
            try:
                tracker.step()
            except Exception:   # one bad poll must not end the recording
                log_error(folder)
            time.sleep(cfg["poll_seconds"])
    except KeyboardInterrupt:
        pass
    finally:
        events.write("session_end")
        lock.unlink(missing_ok=True)


def main():
    folder = core.data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    cfg = core.load_config(folder)
    events.LOG_DIR = folder / "logs"
    multi.STATE = folder / "multi_state.json"
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    pid = other_instance(folder / "advisor.lock")
    if arg == "--once":
        predictor_tick(folder, cfg, Shared(), act=False)
        status.render(folder, cfg, events.read_all())
    elif arg == "--stop":
        stop_instance(folder, pid)
    elif arg == "--share":
        share(folder)
    elif pid:
        second_launch(folder, cfg, pid)
    elif consent(folder, cfg):
        run(folder, cfg)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log_error(core.data_dir())
        raise
