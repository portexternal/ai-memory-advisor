"""Count-table predictor: P(target app starts within WINDOW minutes | weekday, time bucket, previous app).
Trained from the real behavior log (app_start events). Naive-Bayes-style blend of two tables,
Laplace-smoothed with a pessimistic prior so an empty table never fires."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta

SLOT_MIN = 15
BUCKET_SLOTS = 2          # 30-minute buckets
PRIOR_HITS = 0.1          # pseudo-count for "yes"; empty table -> P = PRIOR_HITS / (1 + PRIOR_HITS)
TIME_WEIGHT = 0.7
COVERAGE_MIN = 15         # a slot counts as observed if any event landed within this many minutes


def launches_from_events(events: list, target: str) -> list:
    """[(datetime, app)] for every app_start, oldest first."""
    out = []
    for ev in events:
        if ev.get("type") == "app_start":
            out.append((datetime.fromisoformat(ev["ts"]).replace(tzinfo=None), ev["app"]))
    return out


def prev_app(launches: list, t: datetime, target: str, lookback_min: int = 60) -> str:
    recent = [a for ts, a in launches if t - timedelta(minutes=lookback_min) <= ts < t and a != target]
    return recent[-1] if recent else "none"


def features(t: datetime, launches: list, target: str) -> dict:
    slot = (t.hour * 60 + t.minute) // SLOT_MIN
    return {"wd": t.weekday(), "bucket": slot // BUCKET_SLOTS, "prev": prev_app(launches, t, target)}


def covered_slots(events: list) -> set:
    """Slot start times the logger was alive for, so unobserved time is not treated as 'no launch'."""
    covered = set()
    for ev in events:
        t = datetime.fromisoformat(ev["ts"]).replace(tzinfo=None)
        base = t.replace(minute=(t.minute // SLOT_MIN) * SLOT_MIN, second=0, microsecond=0)
        for k in range(-COVERAGE_MIN // SLOT_MIN, COVERAGE_MIN // SLOT_MIN + 1):
            covered.add(base + timedelta(minutes=k * SLOT_MIN))
    return covered


class CountModel:
    def __init__(self, target: str, window_min: int):
        self.target, self.window = target, window_min
        self.time = defaultdict(Counter)
        self.prev = defaultdict(Counter)
        self.rows = 0
        self.positives = 0

    def fit(self, events: list) -> "CountModel":
        launches = launches_from_events(events, self.target)
        targets = [ts for ts, a in launches if a == self.target]
        for slot in sorted(covered_slots(events)):
            if slot > datetime.now():
                continue
            y = any(slot <= c < slot + timedelta(minutes=self.window) for c in targets)
            f = features(slot, launches, self.target)
            self.time[(f["wd"], f["bucket"])][y] += 1
            self.prev[f["prev"]][y] += 1
            self.rows += 1
            self.positives += int(y)
        return self

    @staticmethod
    def _p(counter: Counter) -> float:
        return (counter[True] + PRIOR_HITS) / (counter[True] + counter[False] + 1.0)

    def predict(self, f: dict) -> float:
        p_time = self._p(self.time[(f["wd"], f["bucket"])])
        p_prev = self._p(self.prev[f["prev"]])
        return TIME_WEIGHT * p_time + (1 - TIME_WEIGHT) * p_prev

    def explain(self, f: dict) -> dict:
        t, p = self.time[(f["wd"], f["bucket"])], self.prev[f["prev"]]
        return {"time_yes": t[True], "time_no": t[False], "prev_yes": p[True], "prev_no": p[False],
                "rows": self.rows, "positives": self.positives}
