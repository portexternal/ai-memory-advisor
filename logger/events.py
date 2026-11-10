"""Append-only JSONL event writer, one file per day."""
import json
import time
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "logs"


def write(event_type: str, **fields) -> dict:
    """Append one event with ISO timestamp + epoch. Returns the event dict."""
    now = datetime.now().astimezone()
    event = {"ts": now.isoformat(timespec="seconds"), "epoch": int(time.time()), "type": event_type, **fields}
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"{now.strftime('%Y-%m-%d')}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def read_all() -> list:
    """Every event from every day file, oldest first. Skips corrupt lines."""
    events = []
    if not LOG_DIR.exists():
        return events
    for path in sorted(LOG_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events
