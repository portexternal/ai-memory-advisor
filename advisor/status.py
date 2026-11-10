"""Plain HTML report of what the advisor has learned and done. Self-contained, works offline."""
import html
import json
from datetime import datetime
from pathlib import Path
from statistics import median

from advisor import core

CSS = ("body{font:15px/1.5 Segoe UI,Arial,sans-serif;margin:32px auto;max-width:860px;color:#14283d}"
       "h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 8px}"
       "table{border-collapse:collapse;width:100%}td,th{padding:6px 10px;border-bottom:1px solid #dde5ec;text-align:left}"
       "th{font-weight:600;background:#f3f7fa}.big{font-size:20px;font-weight:600}.muted{color:#5b6b7a}")


def summarize(evs: list) -> dict:
    """Counters and launch timings from the event log."""
    out = {"fired": 0, "hits": 0, "false": 0, "read_gb": 0.0, "paused": 0, "timing": {}}
    for ev in evs:
        kind = ev.get("type")
        if kind == "prefetch_fired":
            out["fired"] += 1
        elif kind == "prediction_hit":
            out["hits"] += 1
        elif kind == "prediction_false":
            out["false"] += 1
        elif kind == "prefetch_paused":
            out["paused"] += 1
        elif kind == "prefetch_done":
            out["read_gb"] += ev.get("read_gb") or 0
        elif kind == "app_start" and ev.get("launch_s") is not None:
            key = "prefetched" if ev.get("prefetched") else "normal"
            out["timing"].setdefault(ev["app"], {"normal": [], "prefetched": []})[key].append(ev["launch_s"])
    return out


def table(headers: list, rows: list) -> str:
    if not rows:
        return '<p class="muted">Nothing yet.</p>'
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def app_rows(folder: Path, evs: list) -> list:
    rows = []
    for exe, count in core.launch_counts(evs).most_common(15):
        size = core.set_bytes(core.fileset_path(folder, exe))
        rows.append([exe, count, f"{size / core.GB:.2f} GB" if size else "not recorded"])
    return rows


def timing_rows(timing: dict) -> list:
    rows = []
    for exe, t in sorted(timing.items()):
        if not t["prefetched"]:
            continue
        normal = f"{median(t['normal']):.1f} s ({len(t['normal'])})" if t["normal"] else "no data"
        rows.append([exe, normal, f"{median(t['prefetched']):.1f} s ({len(t['prefetched'])})"])
    return rows


def plan_rows(folder: Path) -> list:
    path = folder / "last_plan.json"
    if not path.exists():
        return []
    try:
        plan = json.loads(path.read_text(encoding="utf-8")).get("plan", [])
    except (ValueError, OSError):
        return []
    return [[x["exe"], f"{x['p']:.0%}", f"{x['threshold']:.0%}", f"{x['set_gb']} GB",
             f"{x['budget_gb']} GB" if not x["skip"] else "0", x["skip"] or "prefetch"] for x in plan]


def render(folder: Path, cfg: dict, evs: list) -> Path:
    days = core.recorded_days(folder / "logs")
    active = core.phase(days, cfg["observe_days"]) == "active"
    s = summarize(evs)
    headline = (f"Active since day {cfg['observe_days'] + 1}. Today is day {days}." if active
                else f"Learning: day {days} of {cfg['observe_days']}. Nothing is pre-loaded yet.")
    parts = [
        f"<style>{CSS}</style><h1>AI Memory Advisor</h1>",
        f'<p class="muted">Version {core.VERSION}. Report written {datetime.now():%Y-%m-%d %H:%M}. Data folder: {html.escape(str(folder))}</p>',
        f'<p class="big">{headline}</p>',
        "<h2>Apps seen (top 15 by launches)</h2>", table(["App", "Launches", "Launch file set"], app_rows(folder, evs)),
    ]
    if active:
        parts += [
            "<h2>Predictions</h2>",
            table(["Prefetches", "Hits", "False", "Paused for a busy PC", "Read into cache"],
                  [[s["fired"], s["hits"], s["false"], s["paused"], f"{s['read_gb']:.1f} GB"]]),
            "<h2>Last plan</h2>",
            table(["App", "Chance now", "Needed", "Set", "Budget", "Decision"], plan_rows(folder)),
            "<h2>Time to first window, median (number of launches)</h2>",
            table(["App", "Normal launch", "After a prefetch"], timing_rows(s["timing"])),
            '<p class="muted">Time from process start to first visible window, 1 s resolution. Normal launches '
            "mix cold and already-cached starts, so read this as an indication, not a benchmark.</p>",
        ]
    parts.append('<p class="muted">Recorded: program names, start and stop times, idle periods, free memory. '
                 "No window titles, no keystrokes, no file contents. Nothing leaves this computer. "
                 "Run AIMemoryAdvisor.exe again to stop it.</p>")
    out = folder / "status.html"
    out.write_text("<!doctype html><meta charset='utf-8'><title>AI Memory Advisor</title>" + "".join(parts), encoding="utf-8")
    return out
