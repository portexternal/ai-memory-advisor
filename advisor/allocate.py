"""Memory split between competing apps (0.3.0).

Instead of one app winning the whole prefetch budget, the budget is divided by how much each
app was used recently in the current time slot. Three rules shape it:
  * not overconfident: an app takes everything only when it clearly dominates the slot
  * not too strict:    whenever the slot is usually active, something is loaded
  * measured:          every launch is timed, and an app that shows no gain gets less

Pure functions, no I/O except the optional replay command at the bottom:
  python -m advisor.allocate --replay <logs folder> --apps cs2.exe,blender.exe
"""
import argparse
import json
from datetime import datetime, timedelta
from statistics import median

DEFAULTS = {
    "half_life_days": 7,       # a launch loses half its weight every 7 days
    "slot_window_min": 90,     # launches within this many minutes of the current time of day decide the split
    "activity_window_min": 180,    # wider window for "is a heavy app usually opened around now"; 90 missed 42 % of real launches
    "other_daytype_weight": 0.5,   # weekday launches count half on a weekend, and the reverse
    "floor_share": 0.15,       # an app at or above this share always gets a slice
    "solo_share": 0.85,        # only above this share does one app take the whole budget
    "min_activity": 0.2,       # load something when a heavy app opened in this slot on 20 % of recent days
    "min_slice_gb": 0.25,      # slices smaller than this are not worth a disk read
    "gain_min_samples": 5,     # prefetched launches needed before the measured gain is trusted
    "gain_min_seconds": 0.3,   # a prefetch must save at least this much to count as a gain
    "no_gain_weight": 0.5,     # share multiplier for an app whose prefetches showed no gain
}


def tod_gap_min(a: datetime, b: datetime) -> float:
    """Minutes between two times of day, around the clock (23:50 and 00:10 are 20 apart)."""
    gap = abs((a.hour * 60 + a.minute) - (b.hour * 60 + b.minute))
    return min(gap, 1440 - gap)


def slot_weight(now: datetime, then: datetime, cfg: dict, window: str = "slot_window_min") -> float:
    """How much a past moment says about now: same time of day, recent, same kind of day."""
    if then >= now or tod_gap_min(now, then) > cfg[window]:
        return 0.0
    age_days = (now - then).total_seconds() / 86400
    same_daytype = (now.weekday() >= 5) == (then.weekday() >= 5)
    return 0.5 ** (age_days / cfg["half_life_days"]) * (1.0 if same_daytype else cfg["other_daytype_weight"])


def daily_weights(launches: list, exes: set, now: datetime, cfg: dict, window: str = "slot_window_min") -> dict:
    """{(date, exe): weight}. One launch per app per day counts, so a day of restarts is not ten votes."""
    best = {}
    for when, exe in launches:
        if exe not in exes:
            continue
        w = slot_weight(now, when, cfg, window)
        if w > 0:
            key = (when.date(), exe)
            best[key] = max(best.get(key, 0.0), w)
    return best


def shares(launches: list, exes: set, now: datetime, cfg: dict) -> dict:
    """Recency-weighted share of each app in the current slot. Sums to 1; empty when there is no
    evidence. Falls back to the wider activity window when the narrow slot has no launches."""
    for window in ("slot_window_min", "activity_window_min"):
        totals = {}
        for (_day, exe), w in daily_weights(launches, exes, now, cfg, window).items():
            totals[exe] = totals.get(exe, 0.0) + w
        whole = sum(totals.values())
        if whole > 0:
            return {exe: w / whole for exe, w in totals.items()}
    return {}


def activity(event_times: list, launches: list, exes: set, now: datetime, cfg: dict) -> float:
    """Chance that some heavy app is opened in this slot today: weighted share of recent observed
    days on which one was. A day only counts as observed if the logger was alive in the slot.
    The small prior keeps an empty history below min_activity, so day 1 never fires."""
    window = "activity_window_min"
    observed = {}
    for when in event_times:
        w = slot_weight(now, when, cfg, window)
        if w > 0:
            observed[when.date()] = max(observed.get(when.date(), 0.0), w)
    hit_days = {day for (day, _exe) in daily_weights(launches, exes, now, cfg, window)}
    hits = sum(w for day, w in observed.items() if day in hit_days)
    return (hits + 0.05) / (sum(observed.values()) + 0.5)


def hedge(raw: dict, cfg: dict) -> dict:
    """Apply the confidence rules to raw shares. One app takes all only above solo_share;
    otherwise every app at or above floor_share keeps a slice."""
    if not raw:
        return {}
    top = max(raw, key=raw.get)
    if raw[top] >= cfg["solo_share"]:
        return {top: 1.0}
    kept = {exe: s for exe, s in raw.items() if s >= cfg["floor_share"]} or {top: raw[top]}
    whole = sum(kept.values())
    return {exe: s / whole for exe, s in kept.items()}


def split(budget_gb: float, share: dict, set_gb: dict, cfg: dict) -> dict:
    """Divide the budget by share. No app gets more than its own launch set; what it cannot use
    goes to the others. Slices too small to matter are dropped, smallest share first."""
    active = {exe: s for exe, s in share.items() if set_gb.get(exe, 0) > 0}
    while active:
        alloc, remaining, open_ = {}, budget_gb, dict(active)
        while open_:
            whole = sum(open_.values())
            full = [exe for exe, s in open_.items() if remaining * s / whole >= set_gb[exe]]
            if not full:
                alloc.update({exe: remaining * s / whole for exe, s in open_.items()})
                break
            for exe in full:
                alloc[exe] = set_gb[exe]
                remaining -= set_gb[exe]
                del open_[exe]
        small = [exe for exe, gb in alloc.items() if gb < cfg["min_slice_gb"]]
        if not small or len(active) == 1:
            return {exe: round(gb, 3) for exe, gb in alloc.items() if gb >= cfg["min_slice_gb"]}
        del active[min(small, key=lambda exe: active[exe])]
    return {}


def measured_gain(evs: list, exe: str) -> dict:
    """Median time to first window with and without a prefetch, from the app's own launch log."""
    normal = [e["launch_s"] for e in evs if e.get("type") == "app_start" and e.get("app") == exe
              and e.get("launch_s") is not None and not e.get("prefetched")]
    pre = [e["launch_s"] for e in evs if e.get("type") == "app_start" and e.get("app") == exe
           and e.get("launch_s") is not None and e.get("prefetched")]
    out = {"normal_n": len(normal), "prefetched_n": len(pre),
           "normal_s": round(median(normal), 1) if normal else None,
           "prefetched_s": round(median(pre), 1) if pre else None, "saved_s": None}
    if normal and pre:
        out["saved_s"] = round(out["normal_s"] - out["prefetched_s"], 1)
    return out


def gain_weight(gain: dict, cfg: dict) -> float:
    """1.0 until there is enough evidence. An app whose prefetches saved nothing gets a smaller
    share, never zero: time to first window is a coarse measure and must not lock an app out."""
    if gain["prefetched_n"] < cfg["gain_min_samples"] or gain["saved_s"] is None:
        return 1.0
    return 1.0 if gain["saved_s"] >= cfg["gain_min_seconds"] else cfg["no_gain_weight"]


def parse_launches(evs: list) -> list:
    out = []
    for ev in evs:
        if ev.get("type") == "app_start" and ev.get("app"):
            out.append((datetime.fromisoformat(ev["ts"]).replace(tzinfo=None), ev["app"]))
    return out


def parse_times(evs: list) -> list:
    out = []
    for ev in evs:
        try:
            out.append(datetime.fromisoformat(ev["ts"]).replace(tzinfo=None))
        except (KeyError, ValueError):
            continue
    return out


def plan(now: datetime, evs: list, targets: list, budget_gb: float, cfg: dict, skip: dict) -> dict:
    """The whole decision for one moment. targets = [{"exe", "set_gb", "fileset"}];
    skip = {exe: reason} for apps that must not be loaded now (running, just loaded).
    Returns {"activity", "decision", "rows"}; rows carry share, slice and the reason for a zero."""
    cfg = {**DEFAULTS, **cfg}
    exes = {t["exe"] for t in targets}
    launches = parse_launches(evs)
    act = round(activity(parse_times(evs), launches, exes, now, cfg), 3)
    raw = shares(launches, exes, now, cfg)
    gains = {t["exe"]: measured_gain(evs, t["exe"]) for t in targets}
    weighted = {exe: s * gain_weight(gains[exe], cfg) for exe, s in raw.items() if exe not in skip}
    whole = sum(weighted.values())
    hedged = hedge({exe: w / whole for exe, w in weighted.items()}, cfg) if whole > 0 else {}
    quiet = act < cfg["min_activity"]
    slices = {} if quiet else split(budget_gb, hedged, {t["exe"]: t["set_gb"] for t in targets}, cfg)
    rows = []
    for t in sorted(targets, key=lambda t: -raw.get(t["exe"], 0.0)):
        exe = t["exe"]
        why = skip.get(exe) or ("slot is usually quiet" if quiet else "") or \
            ("" if exe in slices else ("not used in this slot lately" if exe not in raw else "share too small"))
        rows.append({"exe": exe, "share": round(raw.get(exe, 0.0), 3), "set_gb": t["set_gb"],
                     "budget_gb": slices.get(exe, 0.0), "skip": "" if exe in slices else why,
                     "fileset": t.get("fileset"), "gain": gains[exe]})
    decision = "quiet slot, nothing loaded" if quiet else ("load" if slices else "nothing to load")
    return {"activity": act, "decision": decision, "budget_gb": budget_gb, "rows": rows}


def replay(evs: list, exes: set, cfg: dict, warmup_days: int = 7, lead_min: int = 10) -> dict:
    """Score the split rule against winner-takes-all on a real log. For every past heavy-app
    launch, the plan is made from the data before it. Score = share of the budget that sat on
    the app the user then opened."""
    cfg = {**DEFAULTS, **cfg}
    launches, times = parse_launches(evs), parse_times(evs)
    if not times:
        return {"launches": 0}
    start, seen, split_cov, winner_cov, quiet = min(times) + timedelta(days=warmup_days), set(), [], [], 0
    for when, exe in launches:
        if exe not in exes or when < start or (when.date(), exe) in seen:
            continue
        seen.add((when.date(), exe))
        now = when - timedelta(minutes=lead_min)
        if activity(times, launches, exes, now, cfg) < cfg["min_activity"]:
            quiet += 1
            split_cov.append(0.0)
            winner_cov.append(0.0)
            continue
        raw = shares(launches, exes, now, cfg)
        split_cov.append(hedge(raw, cfg).get(exe, 0.0))
        winner_cov.append(1.0 if raw and max(raw, key=raw.get) == exe else 0.0)
    n = len(split_cov)
    if not n:
        return {"launches": 0}
    return {"launches": n, "quiet_skipped": quiet,
            "split_mean_cover": round(sum(split_cov) / n, 3), "winner_mean_cover": round(sum(winner_cov) / n, 3),
            "split_nothing_loaded": round(sum(1 for c in split_cov if c == 0) / n, 3),
            "winner_nothing_loaded": round(sum(1 for c in winner_cov if c == 0) / n, 3)}


def main():
    from logger import events
    from pathlib import Path
    ap = argparse.ArgumentParser(description="Replay the split rule on a behaviour log.")
    ap.add_argument("--replay", required=True, help="folder with the daily .jsonl logs")
    ap.add_argument("--apps", required=True, help="comma-separated heavy apps, for example cs2.exe,blender.exe")
    args = ap.parse_args()
    events.LOG_DIR = Path(args.replay)
    print(json.dumps(replay(events.read_all(), {a.strip().lower() for a in args.apps.split(",")}, {}), indent=2))


if __name__ == "__main__":
    main()
