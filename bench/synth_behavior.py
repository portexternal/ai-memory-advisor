"""SIMULATED behavior log + predictor evaluation, for the proposal draft only.
Generates a plausible 20 Aug - 3 Sep 2026 app-launch history for one user of this PC,
trains a count-table predictor on the first 11 days, tests on the last 4, and writes
data/synth/results.json. Every output is labeled simulated; none of it is measured.

Usage: python bench/synth_behavior.py [--seed 42]
"""
import argparse
import json
import random
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "synth" / "results.json"
START, END = datetime(2026, 8, 20), datetime(2026, 9, 3, 23, 59)
TRAIN_END = datetime(2026, 8, 31)
TARGET = "cs2.exe"
WINDOW_MIN = 30          # "CS2 within the next 30 minutes"
SLOT_MIN = 15
THRESHOLD = 0.35
NOISE_SKIP, NOISE_EXTRA, JITTER_MIN = 0.15, 0.20, 25
APPS = ["cs2.exe", "overwatch.exe", "chrome.exe", "cursor.exe", "claude.exe", "spotify.exe", "discord.exe", "notepad.exe"]

# (weekday list, hour, minute, app, probability, duration_min)
ROUTINE = [
    ([0, 1, 2, 3, 4], 9, 10, "chrome.exe", 0.95, 40),
    ([0, 1, 2, 3, 4], 9, 30, "cursor.exe", 0.90, 150),
    ([0, 1, 2, 3, 4], 9, 40, "claude.exe", 0.85, 140),
    ([0, 1, 2, 3, 4], 13, 30, "cursor.exe", 0.80, 180),
    ([0, 1, 2, 3, 4], 14, 0, "notepad.exe", 0.50, 30),
    ([0, 1, 2, 3, 4], 16, 30, "chrome.exe", 0.70, 60),
    ([0, 1, 2, 3], 19, 45, "discord.exe", 0.85, 200),
    ([0, 1, 2, 3], 20, 0, "cs2.exe", 0.75, 150),
    ([4], 20, 30, "discord.exe", 0.80, 220),
    ([4], 21, 0, "overwatch.exe", 0.60, 160),
    ([4], 21, 5, "cs2.exe", 0.35, 140),
    ([5], 11, 0, "chrome.exe", 0.80, 90),
    ([5], 14, 0, "overwatch.exe", 0.70, 180),
    ([5], 19, 40, "discord.exe", 0.85, 220),
    ([5], 20, 0, "cs2.exe", 0.80, 180),
    ([6], 10, 0, "spotify.exe", 0.90, 480),
    ([6], 11, 0, "chrome.exe", 0.75, 120),
    ([6], 21, 0, "cs2.exe", 0.40, 120),
]


def simulate(seed: int) -> list:
    rng = random.Random(seed)
    launches = []
    day = START
    while day <= END:
        for days, hour, minute, app, prob, dur in ROUTINE:
            if day.weekday() not in days or rng.random() > prob or rng.random() < NOISE_SKIP:
                continue
            ts = day.replace(hour=hour, minute=minute) + timedelta(minutes=rng.randint(-JITTER_MIN, JITTER_MIN))
            launches.append({"ts": ts, "app": app, "dur": dur + rng.randint(-15, 25)})
        if rng.random() < NOISE_EXTRA:
            ts = day.replace(hour=rng.randint(8, 23), minute=rng.randint(0, 59))
            launches.append({"ts": ts, "app": rng.choice(APPS), "dur": rng.randint(10, 90)})
        day += timedelta(days=1)
    launches.sort(key=lambda x: x["ts"])
    return launches


def slot_of(ts: datetime) -> int:
    return (ts.hour * 60 + ts.minute) // SLOT_MIN


def prev_app_before(launches: list, ts: datetime):
    recent = [l["app"] for l in launches if ts - timedelta(minutes=60) <= l["ts"] < ts and l["app"] != TARGET]
    return recent[-1] if recent else "none"


def label_slots(launches: list, first: datetime, last: datetime) -> list:
    """One row per 15-min slot: features + whether CS2 starts within the next WINDOW_MIN."""
    cs2_times = [l["ts"] for l in launches if l["app"] == TARGET]
    rows, t = [], first
    while t < last:
        hit = any(t <= c < t + timedelta(minutes=WINDOW_MIN) for c in cs2_times)
        rows.append({"ts": t, "wd": t.weekday(), "slot": slot_of(t), "prev": prev_app_before(launches, t), "y": hit})
        t += timedelta(minutes=SLOT_MIN)
    return rows


class CountModel:
    """Naive-Bayes-style count table: P(y | weekday, slot) blended with P(y | prev app)."""

    def __init__(self, rows: list):
        self.time = defaultdict(Counter)
        self.prev = defaultdict(Counter)
        for r in rows:
            self.time[(r["wd"], r["slot"] // 2)][r["y"]] += 1   # 30-min buckets
            self.prev[r["prev"]][r["y"]] += 1

    @staticmethod
    def _p(counter: Counter) -> float:
        return (counter[True] + 0.5) / (counter[True] + counter[False] + 1.0)

    def predict(self, r: dict) -> float:
        p_time = self._p(self.time[(r["wd"], r["slot"] // 2)])
        p_prev = self._p(self.prev[r["prev"]])
        return 0.7 * p_time + 0.3 * p_prev


def episodes(fired: list) -> list:
    """Merge consecutive firing slots into prefetch episodes: [(start, end), ...]."""
    eps = []
    for f in fired:
        if eps and (f["ts"] - eps[-1][1]) <= timedelta(minutes=SLOT_MIN):
            eps[-1][1] = f["ts"]
        else:
            eps.append([f["ts"], f["ts"]])
    return eps


def evaluate(model: CountModel, rows: list, launches: list) -> dict:
    """Episode-level precision/recall: an episode is a hit if CS2 starts between its
    first fire and WINDOW_MIN after its last fire. Lead = launch - first fire."""
    fired = [r for r in rows if model.predict(r) >= THRESHOLD]
    eps = episodes(fired)
    cs2 = [l["ts"] for l in launches if l["app"] == TARGET and rows[0]["ts"] <= l["ts"] <= rows[-1]["ts"]]
    covered, leads, hit_eps = set(), [], 0
    for start, end in eps:
        hits = [c for c in cs2 if start + timedelta(minutes=2) <= c <= end + timedelta(minutes=WINDOW_MIN)]
        if hits:
            hit_eps += 1
            for c in hits:
                if c not in covered:
                    covered.add(c)
                    leads.append((c - start).total_seconds() / 60)
    tp, fp = len(covered), len(eps) - hit_eps
    precision = hit_eps / max(len(eps), 1)
    recall = tp / max(len(cs2), 1)
    return {
        "launches": len(cs2), "predicted": tp, "missed": len(cs2) - tp, "false_fires": fp, "episodes": len(eps),
        "precision": round(precision, 2), "recall": round(recall, 2),
        "f1": round(2 * precision * recall / max(precision + recall, 1e-9), 2),
        "lead_time_min_mean": round(sum(leads) / len(leads), 1) if leads else 0.0,
    }


def cross_validate(launches: list) -> dict:
    """Leave-one-day-out over the whole period; sums the per-day confusion counts."""
    total = Counter()
    leads = []
    day = START
    while day <= END:
        nxt = day + timedelta(days=1)
        train_rows = label_slots(launches, START, day) + label_slots(launches, nxt, END + timedelta(minutes=1))
        test_rows = label_slots(launches, day, nxt)
        r = evaluate(CountModel(train_rows), test_rows, launches)
        for k in ("launches", "predicted", "missed", "false_fires", "episodes"):
            total[k] += r[k]
        if r["predicted"]:
            leads.append(r["lead_time_min_mean"])
        day = nxt
    hit_eps = total["episodes"] - total["false_fires"]
    precision = hit_eps / max(total["episodes"], 1)
    recall = total["predicted"] / max(total["launches"], 1)
    return {
        **dict(total), "precision": round(precision, 2), "recall": round(recall, 2),
        "f1": round(2 * precision * recall / max(precision + recall, 1e-9), 2),
        "lead_time_min_mean": round(sum(leads) / len(leads), 1) if leads else 0.0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    launches = simulate(args.seed)
    train = label_slots(launches, START, TRAIN_END)
    test = label_slots(launches, TRAIN_END, END)
    model = CountModel(train)
    result = {
        "simulated": True,
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "seed": args.seed, "period": [START.date().isoformat(), END.date().isoformat()],
        "train_days": (TRAIN_END - START).days, "test_days": (END - TRAIN_END).days + 1,
        "window_min": WINDOW_MIN, "threshold": THRESHOLD,
        "launches": [{"ts": l["ts"].isoformat(timespec="minutes"), "app": l["app"], "dur": l["dur"]} for l in launches],
        "per_app": dict(Counter(l["app"] for l in launches)),
        "cs2_by_weekday_hour": Counter(f"{l['ts'].weekday()}-{l['ts'].hour}" for l in launches if l["app"] == TARGET),
        "test": evaluate(model, test, launches),
        "train_fit": evaluate(model, train, launches),
        "cross_val": cross_validate(launches),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("per_app", "cross_val")}, indent=2))


if __name__ == "__main__":
    main()
