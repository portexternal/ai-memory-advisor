"""Multi-application predictive memory manager (future-work scaffolding, 2026-11-11).

Extends the single-target predictor to N targets without touching the measured CS2 path:
  * one CountModel per target (same features: weekday x 30-min bucket x previous app)
  * independent launch probabilities per target
  * dynamic prefetch budget = fraction of RAM that is free right now, split between likely
    targets in proportion to probability x expected benefit (recorded launch-set size)
  * performance protection: never fire while a heavy app runs, CPU is busy, or the disk is busy
  * continuous learning: per-target false-fire rate (EWMA) raises that target's threshold

Core design principle: never sacrifice present performance for predicted future performance.

Usage:
  python predictor/multi.py --once      # score every target, print the plan, do not prefetch
  python predictor/multi.py --fire      # run the plan once (prefetch), then exit
  python predictor/multi.py             # loop forever (tick_seconds from config)

Targets live in config.json -> predictor.multi.targets. A target with "fileset": null has no
recorded launch file set yet (run bench/capture_fileset.ps1 for it) and is scored but never
prefetched. Everything here is additive: predictor/daemon.py keeps working unchanged.
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psutil  # noqa: E402

from logger import events  # noqa: E402
from predictor.model import CountModel, features, launches_from_events  # noqa: E402

PREFETCH = ROOT / "bench" / "prefetch.py"
STATE = ROOT / "data" / "predictor" / "multi_state.json"
GB = 1e9


# ---------------------------------------------------------------- config / state

def load_cfg() -> dict:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))["predictor"]
    multi = cfg.get("multi") or {}
    # single-target config is the fallback so the file works even without a multi block
    targets = multi.get("targets") or [{"exe": cfg["target"], "fileset": "data/fileset/cs2_launch.json", "enabled": True}]
    return {
        "targets": [t for t in targets if t.get("enabled", True)],
        "window_min": multi.get("window_min", cfg["window_min"]),
        "threshold": multi.get("threshold", cfg["threshold"]),
        "cooldown_min": multi.get("cooldown_min", cfg["cooldown_min"]),
        "min_free_gb": multi.get("min_free_gb", cfg["min_free_gb"]),
        "budget_fraction": multi.get("budget_fraction", 0.5),
        "budget_cap_gb": multi.get("budget_cap_gb", cfg["budget_gb"]),
        "heavy_apps": [a.lower() for a in multi.get("heavy_apps", [])],
        "max_cpu_percent": multi.get("max_cpu_percent", 60),
        "max_disk_mb_s": multi.get("max_disk_mb_s", 50),
        "false_rate_alpha": multi.get("false_rate_alpha", 0.2),
        "false_rate_penalty": multi.get("false_rate_penalty", 0.3),
        "tick_seconds": multi.get("tick_seconds", cfg["tick_seconds"]),
    }


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"targets": {}}


def save_state(state: dict):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1), encoding="utf-8")


def target_state(state: dict, exe: str) -> dict:
    return state["targets"].setdefault(exe, {"last_fire": None, "open_fire": None, "false_rate": 0.0, "hits": 0, "false": 0})


# ---------------------------------------------------------------- scoring

def fileset_bytes(target: dict) -> int:
    if not target.get("fileset"):
        return 0
    path = ROOT / target["fileset"]
    if not path.exists():
        return 0
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("total_bytes", 0))
    except (ValueError, OSError):
        return 0


def score_all(cfg: dict, evs: list) -> list:
    """One independent probability per target, each from its own count table."""
    now = datetime.now()
    out = []
    for t in cfg["targets"]:
        exe = t["exe"].lower()
        model = CountModel(exe, cfg["window_min"]).fit(evs)
        f = features(now, launches_from_events(evs, exe), exe)
        out.append({"exe": exe, "p": round(model.predict(f), 3), "features": f,
                    "rows": model.rows, "positives": model.positives, "fileset": t.get("fileset"),
                    "set_gb": round(fileset_bytes(t) / GB, 2)})
    return out


# ---------------------------------------------------------------- protection

def running_set() -> set:
    return {(p.info["name"] or "").lower() for p in psutil.process_iter(["name"])}


def disk_mb_s(sample_s: float = 1.0) -> float:
    a = psutil.disk_io_counters()
    time.sleep(sample_s)
    b = psutil.disk_io_counters()
    return ((b.read_bytes - a.read_bytes) + (b.write_bytes - a.write_bytes)) / sample_s / 1e6


def system_guard(cfg: dict) -> str:
    """Return '' when it is safe to prefetch, else the reason. Present performance wins."""
    running = running_set()
    heavy = sorted(running & set(cfg["heavy_apps"]))
    if heavy:
        return f"heavy app running: {', '.join(heavy)}"
    cpu = psutil.cpu_percent(interval=0.5)
    if cpu > cfg["max_cpu_percent"]:
        return f"cpu busy {cpu:.0f}%"
    io = disk_mb_s()
    if io > cfg["max_disk_mb_s"]:
        return f"disk busy {io:.0f} MB/s"
    return ""


def free_budget_gb(cfg: dict) -> float:
    free = psutil.virtual_memory().available / GB
    spendable = max(0.0, free - cfg["min_free_gb"]) * cfg["budget_fraction"]
    return round(min(spendable, cfg["budget_cap_gb"]), 2)


# ---------------------------------------------------------------- allocation

def effective_threshold(cfg: dict, ts: dict) -> float:
    """Learning: a target that fired falsely lately needs a higher probability next time."""
    return round(min(0.95, cfg["threshold"] + cfg["false_rate_penalty"] * ts.get("false_rate", 0.0)), 3)


def allocate(cfg: dict, scores: list, state: dict, budget_gb: float, running: set) -> list:
    """Split the budget between likely targets by p x benefit (benefit ~ recorded set size).
    A target already running, cooling down, or without a file set gets 0."""
    now = datetime.now()
    plan = []
    for s in scores:
        ts = target_state(state, s["exe"])
        thr = effective_threshold(cfg, ts)
        reason = ""
        if s["p"] < thr:
            reason = f"p {s['p']} < threshold {thr}"
        elif s["exe"] in running:
            reason = "target running"
        elif not s["set_gb"]:
            reason = "no launch file set recorded"
        elif ts["last_fire"] and now - datetime.fromisoformat(ts["last_fire"]) < timedelta(minutes=cfg["cooldown_min"]):
            reason = "cooldown"
        plan.append({**s, "threshold": thr, "skip": reason, "weight": 0.0, "budget_gb": 0.0})
    live = [x for x in plan if not x["skip"]]
    total = sum(x["p"] * x["set_gb"] for x in live) or 1.0
    for x in live:
        x["weight"] = round(x["p"] * x["set_gb"] / total, 3)
        # never read more than the set itself; leftover budget is simply not spent
        x["budget_gb"] = round(min(x["set_gb"], budget_gb * x["weight"]), 2)
    return plan


# ---------------------------------------------------------------- learning

def settle(cfg: dict, evs: list, state: dict):
    """Close open fires: a launch inside the window = hit, else a false fire. Update EWMA."""
    now = datetime.now()
    for exe, ts in state["targets"].items():
        if not ts.get("open_fire"):
            continue
        fired = datetime.fromisoformat(ts["open_fire"])
        starts = [datetime.fromisoformat(e["ts"]).replace(tzinfo=None) for e in evs
                  if e.get("type") == "app_start" and e.get("app") == exe]
        hit = next((t for t in starts if fired <= t <= fired + timedelta(minutes=cfg["window_min"])), None)
        if hit:
            ts["hits"] += 1
            ts["false_rate"] = round((1 - cfg["false_rate_alpha"]) * ts["false_rate"], 3)
            events.write("prediction_hit", app=exe, lead_min=round((hit - fired).total_seconds() / 60, 1), mode="multi")
            ts["open_fire"] = None
        elif now > fired + timedelta(minutes=cfg["window_min"]):
            ts["false"] += 1
            ts["false_rate"] = round((1 - cfg["false_rate_alpha"]) * ts["false_rate"] + cfg["false_rate_alpha"], 3)
            events.write("prediction_false", app=exe, window_min=cfg["window_min"], mode="multi")
            ts["open_fire"] = None


# ---------------------------------------------------------------- acting

def run_prefetch(target: dict, budget_gb: float, label: str) -> dict:
    cmd = [sys.executable, str(PREFETCH), "--label", label, "--budget-gb", str(budget_gb),
           "--fileset", str(ROOT / target["fileset"])]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=1800).stdout
    start = out.find("{")
    try:
        return json.loads(out[start:]) if start >= 0 else {"error": out[-200:]}
    except ValueError:
        return {"error": out[-200:]}


def execute(cfg: dict, plan: list, state: dict, label: str) -> list:
    done = []
    for x in plan:
        if x["skip"] or x["budget_gb"] <= 0:
            continue
        # re-check the guard before every target: a heavy app may have started meanwhile
        guard = system_guard(cfg)
        if guard:
            events.write("prefetch_paused", app=x["exe"], reason=guard, mode="multi")
            break
        ts = target_state(state, x["exe"])
        events.write("prefetch_fired", app=x["exe"], p=x["p"], budget_gb=x["budget_gb"], weight=x["weight"],
                     label=label, mode="multi", **x["features"])
        rec = run_prefetch(x, x["budget_gb"], label)
        events.write("prefetch_done", app=x["exe"], read_gb=rec.get("read_gb"), seconds=rec.get("seconds"),
                     cpu_seconds=rec.get("cpu_seconds"), error=rec.get("error"), mode="multi")
        ts["last_fire"] = datetime.now().isoformat(timespec="seconds")
        ts["open_fire"] = ts["last_fire"]
        done.append({"exe": x["exe"], **rec})
    save_state(state)
    return done


def tick(cfg: dict, state: dict, act: bool, verbose: bool = False) -> dict:
    evs = events.read_all()
    settle(cfg, evs, state)
    scores = score_all(cfg, evs)
    budget = free_budget_gb(cfg)
    guard = system_guard(cfg)
    plan = allocate(cfg, scores, state, budget, running_set())
    report = {"time": datetime.now().isoformat(timespec="seconds"), "budget_gb": budget, "guard": guard or "ok",
              "plan": [{k: v for k, v in x.items() if k != "features"} for x in plan]}
    if verbose:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    if act and not guard:
        report["executed"] = execute(cfg, plan, state, "auto")
    save_state(state)
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="score and plan only")
    ap.add_argument("--fire", action="store_true", help="run the plan once, then exit")
    args = ap.parse_args()
    cfg, state = load_cfg(), load_state()
    if args.once:
        tick(cfg, state, act=False, verbose=True)
        return
    if args.fire:
        print(json.dumps(tick(cfg, state, act=True, verbose=True).get("executed", []), indent=2))
        return
    events.write("predictor_start", mode="multi", targets=[t["exe"] for t in cfg["targets"]], threshold=cfg["threshold"])
    while True:
        try:
            tick(cfg, state, act=True)
        except Exception as exc:  # keep the daemon alive, record the failure
            events.write("predictor_error", error=str(exc)[:200], mode="multi")
        time.sleep(cfg["tick_seconds"])


if __name__ == "__main__":
    main()
