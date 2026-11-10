"""Thin Win32 helpers: foreground process, idle time. User-mode only, no driver."""
import ctypes
from ctypes import wintypes

import psutil

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def idle_seconds() -> float:
    """Seconds since last keyboard/mouse input (GetLastInputInfo)."""
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    elapsed_ms = kernel32.GetTickCount() - info.dwTime
    return max(0.0, elapsed_ms / 1000.0)


def foreground_process():
    """Return (pid, exe_name_lower, window_title) for the foreground window, or None."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return None
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    try:
        name = psutil.Process(pid.value).name().lower()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None
    return pid.value, name, buf.value


def running_process_names(names_of_interest):
    """Map lower-case exe name -> first pid, only for names in names_of_interest."""
    wanted = set(names_of_interest)
    found = {}
    for proc in psutil.process_iter(["name", "pid"]):
        name = (proc.info["name"] or "").lower()
        if name in wanted and name not in found:
            found[name] = proc.info["pid"]
    return found


ENUM_WINDOWS_PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


def windowed_process_names(ignore=()):
    """Map lower-case exe name -> pid for every process owning a visible, titled
    top-level window. This is what a human sees as 'apps that are open'."""
    skip = set(ignore)
    found = {}

    @ENUM_WINDOWS_PROC
    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindowTextLengthW(hwnd) == 0:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            name = psutil.Process(pid.value).name().lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return True
        if name not in skip and name not in found:
            found[name] = pid.value
        return True

    user32.EnumWindows(visit, 0)
    return found
