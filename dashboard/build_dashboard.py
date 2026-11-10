"""Turn data/logs/*.jsonl into one self-contained dashboard/index.html.
No CDN, no server: works offline from file://."""
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from logger import events  # noqa: E402

TEMPLATE = ROOT / "dashboard" / "template.html"
OUTPUT = ROOT / "dashboard" / "index.html"
MAX_RECENT = 300
MAX_SEGMENT_SECONDS = 3600
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
BOUNDARY_TYPES = ("focus", "idle_start", "session_end", "session_start")


def _dt(ev: dict) -> datetime:
    return datetime.fromisoformat(ev["ts"])


def focus_segments(evs: list) -> list:
    """List of (start_event, end_epoch) for every focus period, capped at one hour."""
    segments = []
    prev = None
    for ev in evs:
        if ev["type"] not in BOUNDARY_TYPES:
            continue
        if prev and prev["type"] == "focus":
            segments.append((prev, min(ev["epoch"], prev["epoch"] + MAX_SEGMENT_SECONDS)))
        prev = ev
    return segments


def hourly_focus(evs: list) -> dict:
    """Seconds of foreground time per app per hour-of-day."""
    table = defaultdict(lambda: [0] * 24)
    for start, end in focus_segments(evs):
        table[start["app"]][_dt(start).hour] += end - start["epoch"]
    return dict(table)


def transitions(evs: list) -> list:
    """Counts of consecutive distinct focus apps: prev -> next."""
    counts = Counter()
    prev = None
    for ev in evs:
        if ev["type"] != "focus":
            continue
        if prev and prev != ev["app"]:
            counts[(prev, ev["app"])] += 1
        prev = ev["app"]
    return [{"from": a, "to": b, "n": n} for (a, b), n in counts.most_common(40)]


def launches(evs: list, app: str = "cs2.exe") -> list:
    return [
        {"ts": ev["ts"], "weekday": WEEKDAYS[_dt(ev).weekday()], "hour": _dt(ev).hour}
        for ev in evs
        if ev["type"] == "app_start" and ev["app"] == app
    ]


def day_timeline(evs: list) -> dict:
    """Per day: list of [start_epoch, end_epoch, app] focus segments."""
    days = defaultdict(list)
    for start, end in focus_segments(evs):
        days[_dt(start).strftime("%Y-%m-%d")].append([start["epoch"], end, start["app"]])
    return dict(days)


def summarize(evs: list) -> dict:
    days = sorted({ev["ts"][:10] for ev in evs})
    weather = [ev for ev in evs if ev["type"] == "weather"]
    return {
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "days_logged": len(days),
        "first_day": days[0] if days else None,
        "last_day": days[-1] if days else None,
        "event_count": len(evs),
        "type_counts": dict(Counter(ev["type"] for ev in evs)),
        "hourly": hourly_focus(evs),
        "transitions": transitions(evs),
        "cs2_launches": launches(evs),
        "timeline": day_timeline(evs),
        "latest_weather": weather[-1] if weather else None,
        "recent": evs[-MAX_RECENT:][::-1],
    }


def build() -> Path:
    data = summarize(events.read_all())
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/", payload)
    OUTPUT.write_text(html, encoding="utf-8")
    return OUTPUT


if __name__ == "__main__":
    print(build())
