"""Tests for the standalone advisor. Run from the repo root: python -m pytest tests -q"""
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from advisor import app, core, status  # noqa: E402
from logger import events  # noqa: E402
from predictor import multi  # noqa: E402

WINDIR = r"C:\Windows"


@pytest.fixture
def folder(tmp_path, monkeypatch):
    """A private data folder; the event log and predictor state point into it."""
    monkeypatch.setattr(events, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(multi, "STATE", tmp_path / "multi_state.json")
    return tmp_path


def make_files(root: Path, sizes: dict) -> dict:
    paths = {}
    for name, size in sizes.items():
        path = root / name
        path.write_bytes(b"x" * size)
        paths[name] = str(path)
    return paths


def write_days(folder: Path, days: int):
    (folder / "logs").mkdir(exist_ok=True)
    for i in range(days):
        day = datetime.now() - timedelta(days=i)
        (folder / "logs" / f"{day:%Y-%m-%d}.jsonl").write_text(
            json.dumps({"ts": day.astimezone().isoformat(timespec="seconds"), "type": "heartbeat"}) + "\n", encoding="utf-8")


def test_phase_is_learning_through_day_ten_and_active_on_day_eleven():
    assert core.phase(1, 10) == "learning"
    assert core.phase(10, 10) == "learning"
    assert core.phase(11, 10) == "active"


def test_recorded_days_counts_only_days_with_data(folder):
    write_days(folder, 3)
    (folder / "logs" / "2020-01-01.jsonl").write_text("", encoding="utf-8")
    assert core.recorded_days(folder / "logs") == 3
    assert core.recorded_days(folder / "missing") == 0


def test_load_config_writes_defaults_then_merges_user_values(folder):
    assert core.load_config(folder)["observe_days"] == 10
    (folder / "config.json").write_text(json.dumps({"observe_days": 3}), encoding="utf-8")
    cfg = core.load_config(folder)
    assert cfg["observe_days"] == 3 and cfg["tick_seconds"] == 600


def test_is_user_file_rejects_windows_folder_and_missing_files(tmp_path):
    real = make_files(tmp_path, {"a.bin": 10})["a.bin"]
    assert core.is_user_file(real, WINDIR)
    assert not core.is_user_file(os.path.join(WINDIR, "explorer.exe"), WINDIR)
    assert not core.is_user_file(str(tmp_path / "nope.bin"), WINDIR)
    assert not core.is_user_file("", WINDIR)


def test_merge_fileset_counts_launches_and_keeps_every_file_while_young(folder):
    paths = make_files(folder, {"a.bin": 100, "b.bin": 50})
    fs = core.fileset_path(folder, "Game.EXE")
    core.merge_fileset(fs, "game.exe", {paths["a.bin"]})
    data = core.merge_fileset(fs, "game.exe", {paths["a.bin"], paths["b.bin"]})
    assert data["launches"] == 2 and data["seen"][paths["a.bin"]] == 2
    assert data["total_bytes"] == 150 and fs.name == "game.exe.json"


def test_stable_files_drops_rare_files_and_orders_by_seen_then_size(folder):
    paths = make_files(folder, {"core.bin": 100, "big.bin": 500, "rare.bin": 900})
    fs = core.fileset_path(folder, "game.exe")
    core.merge_fileset(fs, "game.exe", {paths["core.bin"], paths["big.bin"], paths["rare.bin"]})
    for _ in range(5):
        data = core.merge_fileset(fs, "game.exe", {paths["core.bin"], paths["big.bin"]})
    assert [p for p, _ in core.stable_files(data)] == [paths["big.bin"], paths["core.bin"]]
    assert data["total_bytes"] == 600


def test_pick_targets_needs_enough_launches_and_a_big_enough_set(folder):
    paths = make_files(folder, {"heavy.bin": 3_000_000, "light.bin": 10})
    core.merge_fileset(core.fileset_path(folder, "heavy.exe"), "heavy.exe", {paths["heavy.bin"]})
    core.merge_fileset(core.fileset_path(folder, "light.exe"), "light.exe", {paths["light.bin"]})
    core.merge_fileset(core.fileset_path(folder, "once.exe"), "once.exe", {paths["heavy.bin"]})
    evs = [{"type": "app_start", "app": a} for a in ["heavy.exe"] * 4 + ["light.exe"] * 9 + ["once.exe"]]
    cfg = {**core.DEFAULTS, "min_set_mb": 1, "min_launches": 3}
    targets = core.pick_targets(cfg, folder, evs)
    assert [t["exe"] for t in targets] == ["heavy.exe"] and targets[0]["launches"] == 4


def test_build_multi_cfg_marks_only_big_sets_as_heavy():
    targets = [{"exe": "big.exe", "set_gb": 5.0}, {"exe": "small.exe", "set_gb": 0.4}]
    mcfg = core.build_multi_cfg(core.DEFAULTS, targets)
    assert mcfg["heavy_apps"] == ["big.exe"] and mcfg["budget_cap_gb"] > 0


def test_prefetch_respects_budget(folder):
    paths = make_files(folder, {"a.bin": 4000, "b.bin": 4000})
    fs = core.fileset_path(folder, "game.exe")
    core.merge_fileset(fs, "game.exe", set(paths.values()))
    rec = core.prefetch_fileset(fs, 5000 / core.GB)
    assert rec["stopped"] is False and rec["seconds"] >= 0
    assert core.read_file(paths["a.bin"], 4000, 1000) == 1000


def test_prefetch_stops_when_asked(folder, monkeypatch):
    monkeypatch.setattr(core, "STOP_CHECK_BYTES", 1000)
    paths = make_files(folder, {"a.bin": 2000, "b.bin": 2000, "c.bin": 2000})
    fs = core.fileset_path(folder, "game.exe")
    core.merge_fileset(fs, "game.exe", set(paths.values()))
    rec = core.prefetch_fileset(fs, 1.0, should_stop=lambda: True)
    assert rec["stopped"] is True and rec["read_gb"] == round(2000 / core.GB, 2)


def test_prefetch_reports_a_missing_set(folder):
    assert "error" in core.prefetch_fileset(folder / "nope.json", 1.0)


def test_predictor_does_nothing_while_learning(folder):
    write_days(folder, 10)
    report = app.predictor_tick(folder, core.DEFAULTS, app.Shared(), act=True)
    assert report == {"phase": "learning", "day": 10}
    assert not (folder / "last_plan.json").exists()


def test_predictor_plans_on_day_eleven_without_reading_when_not_acting(folder):
    write_days(folder, 11)
    paths = make_files(folder, {"heavy.bin": 3_000_000})
    core.merge_fileset(core.fileset_path(folder, "heavy.exe"), "heavy.exe", {paths["heavy.bin"]})
    for _ in range(4):
        events.write("app_start", app="heavy.exe", pid=1)
    cfg = {**core.DEFAULTS, "min_set_mb": 1}
    report = app.predictor_tick(folder, cfg, app.Shared(), act=False)
    assert report["phase"] == "active" and report["plan"][0]["exe"] == "heavy.exe"
    assert "executed" not in report and (folder / "last_plan.json").exists()


def test_predictor_reports_when_no_heavy_app_was_learned(folder):
    write_days(folder, 11)
    report = app.predictor_tick(folder, core.DEFAULTS, app.Shared(), act=True)
    assert report["plan"] == [] and "note" in report


def test_shared_remembers_a_recent_prefetch():
    shared = app.Shared()
    assert not shared.recently_fired("a.exe", 30)
    shared.mark_fired("a.exe")
    assert shared.recently_fired("a.exe", 30) and not shared.recently_fired("b.exe", 30)


def test_other_instance_ignores_stale_and_own_pid(folder):
    lock = folder / "advisor.lock"
    assert app.other_instance(lock) == 0
    lock.write_text(str(os.getpid()))
    assert app.other_instance(lock) == 0
    lock.write_text("not a pid")
    assert app.other_instance(lock) == 0


def test_status_summarize_and_render(folder):
    write_days(folder, 11)
    evs = [
        {"type": "app_start", "app": "game.exe", "launch_s": 9.0, "prefetched": False},
        {"type": "app_start", "app": "game.exe", "launch_s": 4.0, "prefetched": True},
        {"type": "prefetch_fired"}, {"type": "prefetch_done", "read_gb": 2.5},
        {"type": "prediction_hit"}, {"type": "prediction_false"}, {"type": "prefetch_paused"},
    ]
    s = status.summarize(evs)
    assert (s["fired"], s["hits"], s["false"], s["paused"], s["read_gb"]) == (1, 1, 1, 1, 2.5)
    assert status.timing_rows(s["timing"]) == [["game.exe", "9.0 s (1)", "4.0 s (1)"]]
    text = status.render(folder, core.DEFAULTS, evs).read_text(encoding="utf-8")
    assert "Active since day 11" in text and "game.exe" in text


def test_share_export_keeps_behaviour_and_drops_identifying_fields(folder):
    paths = make_files(folder, {"secret-name.bin": 3000})
    core.merge_fileset(core.fileset_path(folder, "game.exe"), "game.exe", {paths["secret-name.bin"]})
    evs = [
        {"ts": "2026-03-02T20:15:00+07:00", "epoch": 1, "type": "app_start", "app": "game.exe", "pid": 4242,
         "launch_s": 3.5, "prefetched": True, "title": "private window", "exe": "C:\\Users\\someone\\game.exe"},
        {"ts": "2026-03-04T08:00:00+07:00", "epoch": 2, "type": "session_start", "boot_epoch": 99, "logger_pid": 7},
        {"type": "broken"},
    ]
    out = core.export_share(folder, evs)
    import zipfile
    raw = zipfile.ZipFile(out).read("share.json").decode("utf-8")
    data = json.loads(raw)
    assert data["events"][0] == {"day": 0, "wd": 0, "t": "20:15:00", "type": "app_start", "app": "game.exe",
                                 "launch_s": 3.5, "prefetched": True}
    assert data["events"][1] == {"day": 2, "wd": 2, "t": "08:00:00", "type": "session_start"}
    assert data["filesets"] == [{"app": "game.exe", "launches": 1, "file_count": 1, "total_bytes": 3000}]
    for leak in ("2026-03", "4242", "private window", "someone", "secret-name", str(folder)):
        assert leak not in raw
    assert core.share_id(folder) == data["install_id"] and len(data["install_id"]) == 32


def test_share_command_writes_a_zip_in_test_mode(folder, monkeypatch):
    monkeypatch.setattr(app, "TEST_MODE", True)
    events.write("app_start", app="game.exe", pid=1)
    app.share(folder)
    assert len(list((folder / "share").glob("aima-share-*.zip"))) == 1


def test_status_says_learning_before_day_eleven(folder):
    write_days(folder, 4)
    text = status.render(folder, core.DEFAULTS, []).read_text(encoding="utf-8")
    assert "Learning: day 4 of 10" in text and "Predictions" not in text
