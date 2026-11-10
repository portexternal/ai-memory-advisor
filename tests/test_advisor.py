"""Tests for the standalone advisor. Run from the repo root: python -m pytest tests -q"""
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from advisor import app, core, plat, status  # noqa: E402
from logger import events  # noqa: E402
from predictor import multi  # noqa: E402

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


def test_is_user_file_rejects_system_folders_and_missing_files(tmp_path):
    system = tmp_path / "System"
    system.mkdir()
    inside = make_files(system, {"sys.bin": 10})["sys.bin"]
    real = make_files(tmp_path, {"a.bin": 10})["a.bin"]
    assert core.is_user_file(real, (str(system),))
    assert core.is_user_file(real, str(system))
    assert not core.is_user_file(inside, (str(system).upper(),))
    assert not core.is_user_file(str(tmp_path / "nope.bin"), (str(system),))
    assert not core.is_user_file("", ()) and not core.is_user_file("relative.bin", ())


def test_process_files_works_without_memory_maps_like_on_macos():
    class Opened:
        path = "/a/file"

    class MacProc:
        def open_files(self):
            return [Opened()]

    assert core.process_files(MacProc()) == ["/a/file"]


def test_snapshot_of_own_process_returns_only_user_files(tmp_path):
    held = (tmp_path / "held.bin")
    held.write_bytes(b"x" * 10)
    with held.open("rb"):
        found = core.snapshot_files(os.getpid(), plat.SYSTEM_PREFIXES)
    assert str(held) in found
    assert core.snapshot_files(-1, plat.SYSTEM_PREFIXES) == set()


def test_platform_backend_has_the_whole_interface():
    for name in ("windowed_process_names", "foreground_process", "idle_seconds", "ask_yes_no", "ask_three",
                 "info", "set_autostart", "open_path", "default_data_dir", "bundle_files", "self_exe"):
        assert callable(getattr(plat, name)), name
    assert plat.NAME in ("windows", "macos") and plat.IGNORE and plat.SYSTEM_PREFIXES and plat.RERUN_HINT
    assert plat.bundle_files("/no/bundle/here") == set()
    assert plat.idle_seconds() >= 0 and isinstance(plat.windowed_process_names(), dict)


def test_tracker_logs_a_launch_and_builds_its_file_set(folder, monkeypatch):
    app_file = make_files(folder, {"tool.bin": 500})["tool.bin"]
    open_apps = {}
    monkeypatch.setattr(plat, "windowed_process_names", lambda ignore=(): dict(open_apps))
    monkeypatch.setattr(plat, "foreground_process", lambda: None)
    monkeypatch.setattr(plat, "idle_seconds", lambda: 0.0)
    monkeypatch.setattr(app, "SNAP_DELAYS", (0,))
    monkeypatch.setattr(plat, "bundle_files", lambda exe: set())   # the test runner itself may live in a bundle
    monkeypatch.setattr(core, "snapshot_files", lambda pid, prefixes: {app_file})
    tracker = app.Tracker(folder, {**core.DEFAULTS, "status_seconds": 9999}, app.Shared())
    tracker.step()                                  # first pass: nothing open
    open_apps["tool"] = os.getpid()
    tracker.step()                                  # launch seen, snapshot scheduled
    tracker.step()                                  # snapshot taken and merged
    open_apps.clear()
    tracker.step()
    kinds = [e["type"] for e in events.read_all()]
    assert kinds.count("app_start") == 1 and kinds.count("app_stop") == 1
    assert core.set_bytes(core.fileset_path(folder, "tool")) == 500


def test_report_and_stop_commands_run_quietly_in_test_mode(folder, monkeypatch):
    monkeypatch.setattr(app, "TEST_MODE", True)
    app.open_report(folder, core.DEFAULTS)
    assert (folder / "status.html").exists()
    (folder / "advisor.lock").write_text("0")
    app.stop_with_notice(folder, 0)
    assert not (folder / "advisor.lock").exists()

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


def seed_evening_habit(folder: Path, exe: str, days: int):
    """Past days in which exe was opened at the current time of day (so the slot is active now)."""
    for i in range(1, days + 1):
        day = datetime.now() - timedelta(days=i)
        line = json.dumps({"ts": day.astimezone().isoformat(timespec="seconds"), "type": "app_start", "app": exe})
        with (folder / "logs" / f"{day:%Y-%m-%d}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def heavy_setup(folder: Path, monkeypatch, exes=("heavy.exe",)) -> dict:
    write_days(folder, 11)
    for exe in exes:
        path = make_files(folder, {exe + ".bin": 3_000_000})[exe + ".bin"]
        core.merge_fileset(core.fileset_path(folder, exe), exe, {path})
        seed_evening_habit(folder, exe, 6)
    monkeypatch.setattr(multi, "system_guard", lambda mcfg: "")
    monkeypatch.setattr(multi, "free_budget_gb", lambda mcfg: 4.0)
    monkeypatch.setattr(multi, "running_set", lambda: set())
    return {**core.DEFAULTS, "min_set_mb": 1, "min_slice_gb": 0.001}


def test_predictor_plans_on_day_eleven_without_reading_when_not_acting(folder, monkeypatch):
    cfg = heavy_setup(folder, monkeypatch)
    report = app.predictor_tick(folder, cfg, app.Shared(), act=False)
    assert report["phase"] == "active" and report["decision"] == "load"
    assert report["rows"][0]["exe"] == "heavy.exe" and report["rows"][0]["share"] == 1.0
    assert "executed" not in report and (folder / "last_plan.json").exists()


def test_predictor_loads_every_app_that_shares_the_slot(folder, monkeypatch):
    cfg = heavy_setup(folder, monkeypatch, exes=("one.exe", "two.exe"))
    shared = app.Shared()
    report = app.predictor_tick(folder, cfg, shared, act=True)
    assert sorted(x["exe"] for x in report["executed"]) == ["one.exe", "two.exe"]
    assert all(r["share"] == 0.5 for r in report["rows"])
    kinds = [e["type"] for e in events.read_all()]
    assert kinds.count("prefetch_fired") == 2 and kinds.count("prefetch_done") == 2
    assert shared.recently_fired("one.exe", 30)
    again = app.predictor_tick(folder, cfg, shared, act=True)          # both were just loaded
    assert again["executed"] == [] and {r["skip"] for r in again["rows"]} == {"loaded recently, still cached"}


def test_predictor_respects_the_busy_pc_guard_and_a_running_app(folder, monkeypatch):
    cfg = heavy_setup(folder, monkeypatch, exes=("one.exe", "two.exe"))
    monkeypatch.setattr(multi, "running_set", lambda: {"one.exe"})
    report = app.predictor_tick(folder, cfg, app.Shared(), act=True)
    by_app = {r["exe"]: r for r in report["rows"]}
    assert by_app["one.exe"]["skip"] == "already running" and by_app["two.exe"]["budget_gb"] > 0
    monkeypatch.setattr(multi, "system_guard", lambda mcfg: "cpu busy 90%")
    busy = app.predictor_tick(folder, cfg, app.Shared(), act=True)
    assert busy["guard"] == "cpu busy 90%" and "executed" not in busy


def test_predictor_reports_when_no_heavy_app_was_learned(folder):
    write_days(folder, 11)
    report = app.predictor_tick(folder, core.DEFAULTS, app.Shared(), act=True)
    assert report["rows"] == [] and "note" in report


def test_report_page_shows_the_split(folder, monkeypatch):
    cfg = heavy_setup(folder, monkeypatch, exes=("one.exe", "two.exe"))
    app.predictor_tick(folder, cfg, app.Shared(), act=False)
    text = status.render(folder, cfg, events.read_all()).read_text(encoding="utf-8")
    assert "memory split" in text and "one.exe" in text and "50%" in text and "Budget 4.0 GB" in text

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


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS app bundles only")
def test_mac_bundle_files_lists_a_real_app_bundle():
    apps = sorted(Path("/Applications").glob("*.app"))
    if not apps:
        pytest.skip("no app in /Applications")
    binaries = sorted((apps[0] / "Contents" / "MacOS").iterdir())
    files = plat.bundle_files(str(binaries[0]))
    assert files and all(str(apps[0]) in f for f in files)
    assert str(binaries[0]) in files


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS login item only")
def test_mac_autostart_writes_and_removes_the_launch_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    plat.set_autostart(True, "AIMemoryAdvisor", "/tmp/AIMemoryAdvisor")
    agent = tmp_path / "Library" / "LaunchAgents" / "com.aimemoryadvisor.agent.plist"
    import plistlib
    data = plistlib.loads(agent.read_bytes())
    assert data["ProgramArguments"] == ["/tmp/AIMemoryAdvisor"] and data["RunAtLoad"] is True
    plat.set_autostart(False, "AIMemoryAdvisor", "/tmp/AIMemoryAdvisor")
    assert not agent.exists()

def test_consent_is_remembered_per_terms_version(folder, monkeypatch):
    marker = folder / "consent.json"
    assert not app.consent_on_file(marker)
    marker.write_text(json.dumps({"terms_version": core.TERMS_VERSION}), encoding="utf-8")
    assert app.consent_on_file(marker)
    monkeypatch.setattr(core, "TERMS_VERSION", "9.9")
    assert not app.consent_on_file(marker)
    marker.write_text("broken", encoding="utf-8")
    assert not app.consent_on_file(marker)


def test_consent_asks_shows_the_terms_and_records_the_answer(folder, monkeypatch):
    asked = []
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "set_autostart", lambda on: asked.append(("autostart", on)))
    monkeypatch.setattr(plat, "ask_yes_no", lambda text: asked.append(text) or False)
    assert app.consent(folder, core.DEFAULTS) is False and not (folder / "consent.json").exists()
    assert "Terms of use" in asked[0] and "no warranty" in asked[0] and core.TERMS_VERSION in asked[0]
    monkeypatch.setattr(plat, "ask_yes_no", lambda text: True)
    assert app.consent(folder, core.DEFAULTS) is True
    assert json.loads((folder / "consent.json").read_text())["terms_version"] == core.TERMS_VERSION
    assert ("autostart", True) in asked


def test_share_file_carries_the_sharing_terms_version(folder):
    import zipfile
    data = json.loads(zipfile.ZipFile(core.export_share(folder, [])).read("share.json"))
    assert data["consent"] == {"sharing_terms_version": core.TERMS_VERSION, "agreed_in_app": True}