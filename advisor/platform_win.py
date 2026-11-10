"""Windows backend: open apps, idle time, dialogs, autostart. User mode only."""
import ctypes
import os
import sys
import winreg
from pathlib import Path

from logger import winapi

NAME = "windows"
TITLE = "AI Memory Advisor"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
SYSTEM_PREFIXES = (os.environ.get("WINDIR", r"C:\Windows").lower(),)
IGNORE = ["explorer.exe", "searchhost.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe",
          "lockapp.exe", "textinputhost.exe", "applicationframehost.exe", "aimemoryadvisor.exe"]
RERUN_HINT = "run this file again"
MB_YESNO, MB_YESNOCANCEL, MB_INFO, ID_YES, ID_NO = 4, 3, 0x40, 6, 7

windowed_process_names = winapi.windowed_process_names
foreground_process = winapi.foreground_process
idle_seconds = winapi.idle_seconds


def _box(text: str, flags: int) -> int:
    return ctypes.windll.user32.MessageBoxW(0, text, TITLE, flags | MB_INFO)


def ask_yes_no(text: str) -> bool:
    return _box(text, MB_YESNO) == ID_YES


def ask_three(text: str, labels: tuple) -> int:
    """Three-way question. Returns 0, 1 or 2 for labels[0..2]."""
    answer = _box(f"{text}\n\nYes = {labels[0]}\nNo = {labels[1]}\nCancel = {labels[2]}", MB_YESNOCANCEL)
    return {ID_YES: 0, ID_NO: 1}.get(answer, 2)


def info(text: str):
    _box(text, 0)


def set_autostart(on: bool, app: str, exe: str):
    """Per-user Run entry: no admin needed, removed again on stop."""
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, app, 0, winreg.REG_SZ, f'"{exe}"')
            return
        try:
            winreg.DeleteValue(key, app)
        except FileNotFoundError:
            pass


def open_path(path: Path):
    os.startfile(path)


def default_data_dir(app: str) -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / app


def bundle_files(_exe: str) -> set:
    """Windows apps have no bundle; open and memory-mapped files already cover the launch set."""
    return set()


def self_exe() -> str:
    return sys.executable
