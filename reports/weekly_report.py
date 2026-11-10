"""Weekly behavior report for patient 0: reports/weekly/<year>-W<week>.md.
Covers Monday..Sunday of the week containing the given date (default: today).
Aggregates foreground time, launch times, CS2 pattern, shadow-predictor score
over the week, and a recommendation for the prefetch week."""
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from logger import events  # noqa: E402
from predictor.model import CountModel, features, launches_from_events  # noqa: E402
from reports.daily_report import NOISE, WEEKDAYS, cfg, day_events, focus_minutes, real_launches, shadow_day  # noqa: E402

OUT_DIR = ROOT / "reports" / "weekly"


def week_days(anchor: datetime) -> list:
    monday = anchor - timedelta(days=anchor.weekday())
    return [(monday + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]


def cs2_grid(all_evs: list, days: list, target: str) -> str:
    grid = defaultdict(list)
    for ts, app in launches_from_events(all_evs, target):
        if app == target and ts.strftime("%Y-%m-%d") in days:
            grid[ts.weekday()].append(ts.strftime("%H:%M"))
    return "\n".join(f"| {WEEKDAYS[i]} | {', '.join(grid[i]) or '-'} |" for i in range(7))


def recommendation(all_evs: list, c: dict) -> str:
    model = CountModel(c["target"], c["window_min"]).fit(all_evs)
    fire = defaultdict(list)
    for (wd, bucket), cnt in model.time.items():
        if cnt[True] + cnt[False] == 0:
            continue
        p = model.predict({"wd": wd, "bucket": bucket, "prev": "none"})
        if p >= c["threshold"]:
            fire[wd].append(f"{bucket // 2:02d}:{'30' if bucket % 2 else '00'} (P={p:.2f})")
    lines = [f"| {WEEKDAYS[wd]} | {', '.join(v)} |" for wd, v in sorted(fire.items())]
    return "\n".join(lines) or "| - | no bucket reaches the threshold yet |"


def build(anchor: datetime) -> Path:
    c = cfg()
    all_evs = events.read_all()
    days = week_days(anchor)
    per_app, per_day_rows, shadow_blocks, launches_by_day = Counter(), [], [], Counter()
    for day in days:
        evs = day_events(all_evs, day)
        if not evs:
            per_day_rows.append(f"| {WEEKDAYS[datetime.fromisoformat(day).weekday()]} {day[5:]} | not logged | - | - |")
            continue
        secs, _ = focus_minutes(evs)
        per_app.update(secs)
        active = sum(s for a, s in secs.items() if a not in NOISE) / 60
        cs2 = [e["ts"][11:16] for e in evs if e["type"] == "app_start" and e["app"] == c["target"]]
        launches_by_day[day] = len(real_launches(evs))
        top = next((a for a, _ in secs.most_common() if a not in NOISE), "-")
        per_day_rows.append(f"| {WEEKDAYS[datetime.fromisoformat(day).weekday()]} {day[5:]} | {active:.0f} min | {top} | {', '.join(cs2) or '-'} |")
        shadow_blocks.append(f"### {WEEKDAYS[datetime.fromisoformat(day).weekday()]} {day}\n\n{shadow_day(all_evs, day, c)}")
    top_apps = "\n".join(f"| {a} | {s / 60:.0f} |" for a, s in per_app.most_common(15) if a not in NOISE) or "| - | - |"
    week_id = anchor.strftime("%G-W%V")
    md = f"""# Patient 0, weekly report {week_id} ({days[0]} to {days[-1]})

Generated {datetime.now():%Y-%m-%d %H:%M}. Observation mode all week: nothing was prefetched.

## Days

| Day | Foreground time | Top app | {c['target']} starts |
|---|---|---|---|
{chr(10).join(per_day_rows)}

## Foreground time by app, whole week

| App | Minutes |
|---|---|
{top_apps}

## {c['target']} launch times by weekday

| Weekday | Start times |
|---|---|
{cs2_grid(all_evs, days, c['target'])}

## Shadow predictor, day by day (trained only on days before each day)

{chr(10).join(shadow_blocks) or 'no logged days'}

## Recommendation for the prefetch week

Buckets where the model, trained on everything so far, would fire (threshold {c['threshold']}, window {c['window_min']} min):

| Weekday | Would prefetch at |
|---|---|
{recommendation(all_evs, c)}

Turn the predictor on with `Enable-ScheduledTask ThesisPredictor` followed by `Start-ScheduledTask ThesisPredictor`.
"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{week_id}.md"
    out.write_text(md, encoding="utf-8")
    return out


if __name__ == "__main__":
    when = datetime.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else datetime.now()
    print(build(when))
