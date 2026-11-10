"""macOS backend: open apps, idle time, dialogs, autostart. No special permission needed:
window owner names and pids are readable without Screen Recording access (titles are not, and
are never read)."""
import os
import plistlib
import subprocess
import sys
from pathlib import Path

import psutil
import Quartz

NAME = "macos"
TITLE = "AI Memory Advisor"
AGENT = "com.aimemoryadvisor.agent"
SYSTEM_PREFIXES = ("/system/", "/usr/", "/private/", "/dev/", "/library/apple/")
IGNORE = ["finder", "dock", "windowserver", "systemuiserver", "controlcenter", "control center",
          "notificationcenter", "notification center", "loginwindow", "spotlight", "aimemoryadvisor"]
RERUN_HINT = 'double-click "Open report.command" or "Stop.command" in the same folder'
MAX_BUNDLE_FILES = 20000


def _windows() -> list:
    options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    return Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []


def _app_windows():
    """(pid, name) for every normal on-screen window, front to back."""
    for win in _windows():
        if win.get("kCGWindowLayer", 1) != 0:
            continue
        pid = int(win.get("kCGWindowOwnerPID", 0))
        try:
            yield pid, psutil.Process(pid).name().lower()
        except psutil.Error:
            continue


def windowed_process_names(ignore=()) -> dict:
    """Map lower-case process name -> pid for every app with a visible window."""
    skip, found = set(ignore), {}
    for pid, name in _app_windows():
        if name not in skip and name not in found:
            found[name] = pid
    return found


def foreground_process():
    """(pid, name, '') of the frontmost app window, or None. The title is never read."""
    for pid, name in _app_windows():
        return pid, name, ""
    return None


def idle_seconds() -> float:
    return float(Quartz.CGEventSourceSecondsSinceLastEventType(
        Quartz.kCGEventSourceStateCombinedSessionState, Quartz.kCGAnyInputEventType))


def _dialog(text: str, buttons: tuple, default: str) -> str:
    """Native dialog through osascript. The text travels as an argument, never inside the script."""
    listed = ", ".join(f'"{b}"' for b in buttons)
    script = ["-e", "on run argv",
              "-e", f'display dialog (item 1 of argv) buttons {{{listed}}} default button "{default}" with title "{TITLE}"',
              "-e", "return button returned of result", "-e", "end run"]
    try:
        done = subprocess.run(["osascript", *script, text], capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip()


def ask_yes_no(text: str) -> bool:
    return _dialog(text, ("No", "Yes"), "Yes") == "Yes"


def ask_three(text: str, labels: tuple) -> int:
    """Three-way question. Returns 0, 1 or 2 for labels[0..2]; a closed dialog counts as the last."""
    answer = _dialog(text, (labels[2], labels[1], labels[0]), labels[0])
    return labels.index(answer) if answer in labels else 2


def info(text: str):
    _dialog(text, ("OK",), "OK")


def _agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{AGENT}.plist"


def set_autostart(on: bool, _app: str, exe: str):
    """Per-user LaunchAgent: starts at login, no admin needed, removed again on stop."""
    path = _agent_path()
    if not on:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        plistlib.dump({"Label": AGENT, "ProgramArguments": [exe], "RunAtLoad": True, "ProcessType": "Background"}, fh)


def open_path(path: Path):
    subprocess.run(["open", str(path)], check=False)


def default_data_dir(app: str) -> Path:
    return Path.home() / "Library" / "Application Support" / app


def bundle_files(exe: str) -> set:
    """Files of the .app bundle the executable lives in. macOS does not let a normal user list
    another process's memory-mapped files, so the bundle stands in for them."""
    marker = exe.find(".app/")
    if marker < 0:
        return set()
    found = set()
    for root, _dirs, names in os.walk(exe[:marker + 4]):
        for name in names:
            path = os.path.join(root, name)
            if not os.path.islink(path):
                found.add(path)
        if len(found) >= MAX_BUNDLE_FILES:
            break
    return found


def self_exe() -> str:
    return sys.executable
