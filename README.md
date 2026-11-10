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

## Standalone app (`advisor/`, 2026-11-10)

One exe for any Windows PC, no Python and no admin rights. It watches for 10 recorded days, then starts
predicting and prefetching on day 11 for whatever heavy apps that user has. Launch file sets are captured
automatically (open + memory-mapped files of the app during its first 45 s). Data stays in
`%LOCALAPPDATA%\AIMemoryAdvisor`; no network, no window titles.

```powershell
pip install psutil pyinstaller pytest
python -m pytest tests -q                                        # 18 tests
powershell -ExecutionPolicy Bypass -File advisor\build_exe.ps1   # dist\AIMemoryAdvisor.exe
```

Run the exe once to start (it asks for consent), again to open the report or stop it. Notes for testers:
`advisor/README-for-testers.txt`. What comes next: `ROADMAP.md`.

## Future work: multi-application memory manager (scaffolding, 2026-11-11)

`predictor/multi.py` scores every target in `config.json → predictor.multi.targets` with its own count table, splits a dynamic
budget (fraction of free RAM, capped at 40 % of installed RAM) between likely targets by probability × launch-set size, pauses
while a heavy app runs or CPU / disk are busy, and raises a target's threshold after false fires. Design and status:
`docs/FUTURE-MULTI-APP.md`. Run `python predictor/multi.py --once` to see the plan. The single-target daemon is unchanged.

Settings changed 2026-11-11: trigger threshold 0.35 → 0.30, tick 60 s → 600 s, prefetch budget 24 GB → 12.4 GB
(min of the launch set and 40 % of installed RAM; 24 GB needed 27 GB free on a 32 GB PC and could not fire).
