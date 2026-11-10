"""Daily behavior report (markdown) for patient 0. Run each morning: writes
reports/daily/<yesterday>.md with what happened and what the predictor would have
done in shadow mode, plus a forecast for today. Nothing is prefetched.

Usage: python reports/daily_report.py [YYYY-MM-DD]   (default: yesterday)
"""
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from logger import events  # noqa: E402
from predictor.model import CountModel, features, launches_from_events  # noqa: E402

OUT_DIR = ROOT / "reports" / "daily"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
NOISE = {"explorer.exe", "searchhost.exe", "applicationframehost.exe", "textinputhost.exe", "msedgewebview2.exe",
         "steamwebhelper.exe", "gamebar.exe", "openwith.exe", "systemsettings.exe", "m365copilot.exe", "procmon64.exe",
         "powershell.exe", "windowsterminal.exe", "mspaint.exe"}
MAX_SEG = 3600


def cfg() -> dict:
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))["predictor"]


def day_events(evs: list, day: str) -> list:
    return [e for e in evs if e["ts"][:10] == day]


def focus_minutes(evs: list) -> tuple:
    """(Counter app->seconds, segments [(start_dt, end_dt, app, title)])"""
    secs, segs, prev = Counter(), [], None
    for ev in evs:
        if ev["type"] not in ("focus", "idle_start", "session_end", "session_start", "idle_end"):
            continue
        if prev and prev["type"] == "focus":
            end = min(ev["epoch"], prev["epoch"] + MAX_SEG)
            secs[prev["app"]] += end - prev["epoch"]
            segs.append((datetime.fromtimestamp(prev["epoch"]), datetime.fromtimestamp(end), prev["app"], prev.get("title", "")))
        prev = ev if ev["type"] != "idle_end" else None
    return secs, segs


def hour_strip(segs: list) -> str:
    """One line per hour with the dominant app and its share."""
    by_hour = defaultdict(Counter)
    for s, e, app, _ in segs:
        t = s
        while t < e:
            nxt = min(e, (t + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0))
            by_hour[t.hour][app] += (nxt - t).total_seconds()
            t = nxt
    lines = []
    for h in sorted(by_hour):
        app, sec = by_hour[h].most_common(1)[0]
        total = sum(by_hour[h].values())
        lines.append(f"| {h:02d}:00 | {app} | {sec / 60:.0f} min ({sec / total * 100:.0f}%) | {total / 60:.0f} min active |")
    return "\n".join(lines)


def real_launches(evs: list) -> list:
    """app_start events, minus noise apps and minus the burst a logger restart emits
    for apps that were already open (older logs; newer logs use app_seen)."""
    restarts = [e["epoch"] for e in evs if e["type"] == "session_start"]
    return [e for e in evs if e["type"] == "app_start" and e["app"] not in NOISE
            and not any(0 <= e["epoch"] - r <= 5 for r in restarts)]


def launches_table(evs: list) -> str:
    rows = [f"| {ev['ts'][11:16]} | {ev['app']} |" for ev in real_launches(evs)]
    return "\n".join(rows) or "| - | no launches logged |"


def idle_periods(evs: list) -> str:
    rows, start = [], None
    for ev in evs:
        if ev["type"] == "idle_start":
            start = ev
        elif ev["type"] == "idle_end" and start:
            rows.append(f"| {start['ts'][11:16]} | {ev['ts'][11:16]} | {(ev['epoch'] - start['epoch']) / 60:.0f} min |")
            start = None
    if start:
        rows.append(f"| {start['ts'][11:16]} | (still idle / PC off) | - |")
    return "\n".join(rows) or "| - | - | none |"


def before_target(all_evs: list, day_evs: list, target: str) -> str:
    launches = launches_from_events(all_evs, target)
    rows = []
    for ev in day_evs:
        if ev["type"] == "app_start" and ev["app"] == target:
            t = datetime.fromisoformat(ev["ts"]).replace(tzinfo=None)
            recent = [a for ts, a in launches if t - timedelta(minutes=60) <= ts < t and a != target and a not in NOISE]
            rows.append(f"| {ev['ts'][11:16]} | {', '.join(dict.fromkeys(recent)) or 'nothing new'} |")
    return "\n".join(rows) or f"| - | no {target} launch |"


def shadow_day(all_evs: list, day: str, c: dict) -> str:
    """What the predictor, trained on days before `day`, would have done on `day`."""
    day_start = datetime.fromisoformat(day)
    train = [e for e in all_evs if e["ts"][:10] < day]
    if not train:
        return "No earlier days to train on yet, shadow run skipped."
    model = CountModel(c["target"], c["window_min"]).fit(train)
    launches = launches_from_events(all_evs, c["target"])
    targets = [ts for ts, a in launches if a == c["target"] and ts.date() == day_start.date()]
    fires, t = [], day_start
    while t < day_start + timedelta(days=1):
        if model.predict(features(t, launches, c["target"])) >= c["threshold"]:
            fires.append(t)
        t += timedelta(minutes=15)
    episodes = []
    for f in fires:
        if episodes and f - episodes[-1][1] <= timedelta(minutes=15):
            episodes[-1][1] = f
        else:
            episodes.append([f, f])
    lines = [f"Trained on {model.rows} slots from earlier days ({model.positives} positive). Threshold {c['threshold']}.", ""]
    hits = 0
    for s, e in episodes:
        hit = next((x for x in targets if s <= x <= e + timedelta(minutes=c["window_min"])), None)
        hits += bool(hit)
        lines.append(f"- would prefetch {s:%H:%M}-{e:%H:%M}: " + (f"HIT, {c['target']} started {hit:%H:%M}" if hit else "false fire, 22 GB wasted"))
    missed = [x for x in targets if not any(s <= x <= e + timedelta(minutes=c["window_min"]) for s, e in episodes)]
    for x in missed:
        lines.append(f"- MISS: {c['target']} started {x:%H:%M} with no prefetch before it")
    if not episodes and not targets:
        lines.append("- quiet day: no fires, no launches")
    lines.append("")
    lines.append(f"Score: {hits} hit, {len(missed)} missed, {len(episodes) - hits} false, out of {len(targets)} launch(es).")
    return "\n".join(lines)


def forecast(all_evs: list, day: str, c: dict) -> str:
    """Time-table probability per 30-min bucket for `day`'s weekday, from everything logged so far."""
    model = CountModel(c["target"], c["window_min"]).fit(all_evs)
    wd = datetime.fromisoformat(day).weekday()
    rows = []
    for bucket in range(48):
        cnt = model.time[(wd, bucket)]
        n = cnt[True] + cnt[False]
        if n == 0:
            continue
        p = model.predict({"wd": wd, "bucket": bucket, "prev": "none"})
        flag = "PREFETCH" if p >= c["threshold"] else ""
        rows.append(f"| {bucket // 2:02d}:{'30' if bucket % 2 else '00'} | {cnt[True]}/{n} | {p:.2f} | {flag} |")
    head = f"Based on {model.rows} logged slots. Buckets never observed on a {WEEKDAYS[wd]} are omitted.\n\n| Bucket | CS2 followed / seen | P | Shadow decision |\n|---|---|---|---|\n"
    return head + ("\n".join(rows) or "| - | - | - | nothing observed on this weekday yet |")


def transitions(segs: list) -> str:
    cnt = Counter()
    for a, b in zip(segs, segs[1:]):
        if a[2] != b[2] and a[2] not in NOISE and b[2] not in NOISE:
            cnt[(a[2], b[2])] += 1
    return "\n".join(f"| {a} | {b} | {n} |" for (a, b), n in cnt.most_common(8)) or "| - | - | - |"


def build(day: str) -> Path:
    c = cfg()
    all_evs = events.read_all()
    evs = day_events(all_evs, day)
    d = datetime.fromisoformat(day)
    secs, segs = focus_minutes(evs)
    active = [e for e in evs if e["type"] in ("focus", "app_start")]
    first, last = (active[0]["ts"][11:16], active[-1]["ts"][11:16]) if active else ("-", "-")
    top = "\n".join(f"| {app} | {s / 60:.0f} |" for app, s in secs.most_common(12) if app not in NOISE) or "| - | - |"
    weather = [e for e in evs if e["type"] == "weather"]
    w = f"{weather[0]['temp_c']}°C, code {weather[0]['weather_code']}, {weather[-1]['precip_mm']} mm" if weather else "n/a"
    tomorrow = (d + timedelta(days=1)).strftime("%Y-%m-%d")
    md = f"""# Patient 0, daily report: {WEEKDAYS[d.weekday()]} {day}

Generated {datetime.now():%Y-%m-%d %H:%M}. Observation mode: nothing was prefetched. Events logged this day: {len(evs)}.
First activity {first}, last activity {last}. Weather (first sample): {w}.

## Foreground time by app

| App | Minutes |
|---|---|
{top}

## Hour by hour (dominant app)

| Hour | App | Share | Active |
|---|---|---|---|
{hour_strip(segs)}

## App launches

| Time | App |
|---|---|
{launches_table(evs)}

## What was open in the hour before {c['target']}

| CS2 start | Apps opened in the previous 60 min |
|---|---|
{before_target(all_evs, evs, c['target'])}

## Most common switches (A then B)

| From | To | Count |
|---|---|---|
{transitions(segs)}

## Idle periods (5 min or more without input)

| From | To | Length |
|---|---|---|
{idle_periods(evs)}

## Shadow predictor on this day

{shadow_day(all_evs, day, c)}

## Forecast for {WEEKDAYS[(d + timedelta(days=1)).weekday()]} {tomorrow}

{forecast(all_evs, tomorrow, c)}
"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{day}.md"
    out.write_text(md, encoding="utf-8")
    return out


if __name__ == "__main__":
    target_day = sys.argv[1] if len(sys.argv) > 1 else (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    print(build(target_day))
