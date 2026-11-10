# AI Memory Advisor: predicting the next app launch to pre-warm RAM

Thesis project. Question: can a small per-user model predict which app (target: CS2)
the user will open next, early enough for a user-mode prefetcher to pull that app's
launch files into the Windows file cache, so the launch is measurably faster?

Paging and segmentation stay untouched. No kernel driver, no binary rewriting.
The AI only advises: what to keep, what to prefetch, when.

## Layout

| Path | What |
|------|------|
| `logger/` | Behavior logger daemon (foreground app, app start/stop, idle, heartbeat, weather). Writes `data/logs/YYYY-MM-DD.jsonl`. |
| `dashboard/` | `build_dashboard.py` renders `data/logs` into a self-contained `index.html`. Rebuilt every 5 min by the logger. |
| `scripts/` | `install_startup.ps1` registers logon tasks (logger + open dashboard). `uninstall_startup.ps1` removes them. |
| `bench/` | Launch-latency benchmark (cold vs warm CS2, cache flush, timing). Next step. |
| `docs/PLAN.md` | Experiment design, weekly plan, baselines, risks. |
| `config.json` | Poll rates, watched apps, dashboard-at-logon toggle. |

## Setup (Windows, Python 3.10+)

```powershell
pip install -r requirements.txt
python dashboard\build_dashboard.py          # renders dashboard\index.html
powershell -ExecutionPolicy Bypass -File scripts\install_startup.ps1   # logon tasks + start logger now
```

Run the logger in the foreground for debugging:

```powershell
python logger\behavior_logger.py
```

## Event types

`session_start`, `session_end`, `app_start`, `app_stop`, `focus`, `idle_start`, `idle_end`,
`heartbeat` (RAM usage), `weather`, `weather_error`.

Window titles are off by default (`log_window_titles: false`). Only exe names are stored.

## Standalone app (`advisor/`)

One download for Windows or macOS, no Python and no admin rights: [Releases](https://github.com/portexternal/ai-memory-advisor/releases).
It watches for 10 recorded days, then starts predicting and prefetching on day 11 for whatever heavy apps
that user has. Launch file sets are captured automatically during each app's first 45 seconds. Data stays
on the computer; no network, no window titles.

| | Windows 10 / 11 | macOS 12+ (experimental) |
|---|---|---|
| Open apps and focus | visible top-level windows | on-screen window owners (no Screen Recording permission) |
| Launch file set | open + memory-mapped files | open files + the app bundle |
| Starts at sign-in | per-user Run entry | per-user LaunchAgent |
| Data folder | `%LOCALAPPDATA%\AIMemoryAdvisor` | `~/Library/Application Support/AIMemoryAdvisor` |
| Report / stop | run the exe again | `Open report.command`, `Stop.command` |

**Getting the best result.** It learns a routine, so give it one: leave it running every day, open your heavy
apps at your usual times, use the computer on as many of the 10 learning days as you can, and open each heavy
app at least 3 times. Days without use are skipped, not counted. No fixed routine still works, it just
predicts worse at first; that case is what the opt-in data pool is for (`docs/SHARING.md`).

```
pip install -r requirements.txt pytest pyinstaller
python -m pytest tests -q
powershell -ExecutionPolicy Bypass -File advisor\build_exe.ps1   # Windows: dist\AIMemoryAdvisor.exe
bash advisor/build_mac.sh                                        # macOS: dist/AIMemoryAdvisor-mac/
```

Every push is tested on Windows and on Apple Silicon and Intel Macs (`.github/workflows/build.yml`); the Mac
jobs also run the built app against a real app launch. OS-specific code lives in `advisor/platform_win.py` and
`advisor/platform_mac.py` behind `advisor/plat.py`. Notes for testers: `advisor/README-for-testers*.txt`.
What comes next: `ROADMAP.md`.
## Future work: multi-application memory manager (scaffolding, 2026-11-11)

`predictor/multi.py` scores every target in `config.json → predictor.multi.targets` with its own count table, splits a dynamic
budget (fraction of free RAM, capped at 40 % of installed RAM) between likely targets by probability × launch-set size, pauses
while a heavy app runs or CPU / disk are busy, and raises a target's threshold after false fires. Design and status:
`docs/FUTURE-MULTI-APP.md`. Run `python predictor/multi.py --once` to see the plan. The single-target daemon is unchanged.

Settings changed 2026-11-11: trigger threshold 0.35 → 0.30, tick 60 s → 600 s, prefetch budget 24 GB → 12.4 GB
(min of the launch set and 40 % of installed RAM; 24 GB needed 27 GB free on a 32 GB PC and could not fire).
