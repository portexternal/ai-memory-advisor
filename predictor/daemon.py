"""Predictor daemon. Every tick: retrain on the behavior log, score the current moment,
fire the prefetcher when P(target within window) >= threshold. Scores hits, misses and
false fires afterwards and writes everything as events so the dashboard shows them.

Usage:
  python predictor/daemon.py            # run forever (use pythonw via Task Scheduler)
  python predictor/daemon.py --once     # print the current score and exit
  python predictor/daemon.py --force    # fire the prefetch now (manual 'prediction'), then exit
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

STATE = ROOT / "data" / "predictor" / "state.json"
PREFETCH = ROOT / "bench" / "prefetch.py"


def load_cfg() -> dict:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    return cfg["predictor"]


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    # launches before the daemon first ran are not judged as misses
    return {"last_fire": None, "open_fire": None, "last_seen_launch": datetime.now().astimezone().isoformat(timespec="seconds")}


def save_state(state: dict):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state), encoding="utf-8")


def is_running(exe: str) -> bool:
    return any((p.info["name"] or "").lower() == exe for p in psutil.process_iter(["name"]))


def last_exit(evs: list, exe: str):
    stops = [ev["ts"] for ev in evs if ev.get("type") == "app_stop" and ev.get("app") == exe]
    return datetime.fromisoformat(stops[-1]).replace(tzinfo=None) if stops else None


def run_prefetch(cfg: dict, label: str) -> dict:
    """Run bench/prefetch.py as a subprocess; returns its JSON record (last line block)."""
    cmd = [sys.executable, str(PREFETCH), "--label", label, "--budget-gb", str(cfg["budget_gb"])]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=1800).stdout
    start = out.find("{")
    return json.loads(out[start:]) if start >= 0 else {"error": out[-300:]}


def score_now(cfg: dict, evs: list):
    model = CountModel(cfg["target"], cfg["window_min"]).fit(evs)
    now = datetime.now()
    f = features(now, launches_from_events(evs, cfg["target"]), cfg["target"])
    return model.predict(f), f, model.explain(f)


def settle_open_fire(cfg: dict, evs: list, state: dict):
    """After a fire: did the target start inside the window? Emit hit / false events once."""
    if not state["open_fire"]:
        return
    fired = datetime.fromisoformat(state["open_fire"])
    launches = [datetime.fromisoformat(ev["ts"]).replace(tzinfo=None) for ev in evs
                if ev.get("type") == "app_start" and ev.get("app") == cfg["target"]]
    hit = next((l for l in launches if fired <= l <= fired + timedelta(minutes=cfg["window_min"])), None)
    if hit:
        events.write("prediction_hit", app=cfg["target"], lead_min=round((hit - fired).total_seconds() / 60, 1))
        state["open_fire"] = None
    elif datetime.now() > fired + timedelta(minutes=cfg["window_min"]):
        events.write("prediction_false", app=cfg["target"], window_min=cfg["window_min"])
        state["open_fire"] = None


def note_misses(cfg: dict, evs: list, state: dict):
    """A target launch with no fire in the preceding window is a miss."""
    launches = [ev["ts"] for ev in evs if ev.get("type") == "app_start" and ev.get("app") == cfg["target"]]
    for ts in launches:
        if state["last_seen_launch"] and ts <= state["last_seen_launch"]:
            continue
        t = datetime.fromisoformat(ts).replace(tzinfo=None)
        fired = datetime.fromisoformat(state["last_fire"]) if state["last_fire"] else None
        if not (fired and fired <= t <= fired + timedelta(minutes=cfg["window_min"])):
            events.write("prediction_miss", app=cfg["target"])
    if launches:
        state["last_seen_launch"] = max(launches[-1], state["last_seen_launch"] or "")


def should_fire(cfg: dict, evs: list, state: dict, p: float) -> str:
    """Return '' to fire, else the reason not to."""
    now = datetime.now()
    if p < cfg["threshold"]:
        return "below threshold"
    if is_running(cfg["target"]):
        return "target running"
    if state["last_fire"] and now - datetime.fromisoformat(state["last_fire"]) < timedelta(minutes=cfg["cooldown_min"]):
        return "cooldown"
    exited = last_exit(evs, cfg["target"])
    if exited and now - exited < timedelta(minutes=cfg["cooldown_min"]):
        return "target exited recently, still warm"
    if psutil.virtual_memory().available / 1e9 < cfg["budget_gb"] + cfg["min_free_gb"]:
        return "not enough free RAM"
    return ""


def fire(cfg: dict, state: dict, p: float, f: dict, label: str):
    events.write("prefetch_fired", app=cfg["target"], p=round(p, 3), **f, label=label)
    rec = run_prefetch(cfg, label)
    events.write("prefetch_done", app=cfg["target"], read_gb=rec.get("read_gb"), seconds=rec.get("seconds"),
                 cpu_seconds=rec.get("cpu_seconds"), error=rec.get("error"))
    state["last_fire"] = datetime.now().isoformat(timespec="seconds")
    state["open_fire"] = state["last_fire"]
    save_state(state)
    return rec


def tick(cfg: dict, state: dict, verbose: bool = False):
    evs = events.read_all()
    settle_open_fire(cfg, evs, state)
    note_misses(cfg, evs, state)
    p, f, why = score_now(cfg, evs)
    reason = should_fire(cfg, evs, state, p)
    if verbose:
        print(json.dumps({"p": round(p, 3), "features": f, "table": why, "decision": reason or "FIRE"}, indent=2))
    if not reason:
        fire(cfg, state, p, f, "auto")
    save_state(state)
    return p, reason


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    cfg, state = load_cfg(), load_state()
    if args.force:
        p, f, _ = score_now(cfg, events.read_all())
        print(json.dumps(fire(cfg, state, p, f, "forced"), indent=2))
        return
    if args.once:
        tick(cfg, state, verbose=True)
        return
    events.write("predictor_start", threshold=cfg["threshold"], window_min=cfg["window_min"])
    while True:
        try:
            tick(cfg, state)
        except Exception as exc:  # keep the daemon alive, record the failure
            events.write("predictor_error", error=str(exc)[:200])
        time.sleep(cfg["tick_seconds"])


if __name__ == "__main__":
    main()
