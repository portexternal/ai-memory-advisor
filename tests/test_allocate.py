"""Tests for the memory split between competing apps. Run: python -m pytest tests -q"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from advisor import allocate  # noqa: E402

CFG = allocate.DEFAULTS
CS2, OW = "cs2.exe", "overwatch.exe"
NOW = datetime(2026, 3, 30, 19, 50)          # a Monday evening, start of week 5


def ev(when: datetime, kind: str = "app_start", **fields) -> dict:
    return {"ts": when.isoformat(timespec="seconds"), "type": kind, **fields}


def evening(days_ago: int, app: str) -> dict:
    """One launch at 20:15 that many days before NOW."""
    day = (NOW - timedelta(days=days_ago)).replace(hour=20, minute=15)
    return ev(day, app=app)


def four_week_story() -> list:
    """Week 1 all CS2, week 2 all Overwatch, week 3 alternating, week 4 CS2 3 days then Overwatch 4."""
    evs = [evening(d, CS2) for d in range(22, 29)]
    evs += [evening(d, OW) for d in range(15, 22)]
    evs += [evening(d, CS2 if d % 2 else OW) for d in range(8, 15)]
    evs += [evening(d, CS2) for d in (5, 6, 7)] + [evening(d, OW) for d in (1, 2, 3, 4)]
    return evs


def targets(cs2_gb=12.0, ow_gb=12.0) -> list:
    return [{"exe": CS2, "set_gb": cs2_gb, "fileset": "cs2.json"}, {"exe": OW, "set_gb": ow_gb, "fileset": "ow.json"}]


def test_time_of_day_gap_wraps_around_midnight():
    assert allocate.tod_gap_min(datetime(2026, 1, 1, 23, 50), datetime(2026, 1, 2, 0, 10)) == 20
    assert allocate.tod_gap_min(datetime(2026, 1, 1, 8, 0), datetime(2026, 1, 1, 20, 0)) == 720


def test_slot_weight_halves_per_week_and_ignores_other_times_of_day():
    week_ago = NOW - timedelta(days=7)
    assert allocate.slot_weight(NOW, week_ago, CFG) == pytest.approx(0.5)
    assert allocate.slot_weight(NOW, week_ago.replace(hour=9), CFG) == 0.0
    assert allocate.slot_weight(NOW, NOW + timedelta(minutes=5), CFG) == 0.0
    saturday = NOW - timedelta(days=2)
    assert allocate.slot_weight(NOW, saturday, CFG) == pytest.approx(0.5 ** (2 / 7) * 0.5)


def test_four_week_story_gives_a_near_even_split_leaning_to_the_recent_app():
    plan = allocate.plan(NOW, four_week_story(), targets(), 8.0, {}, {})
    by_app = {r["exe"]: r for r in plan["rows"]}
    assert plan["decision"] == "load"
    assert 0.35 < by_app[CS2]["share"] < 0.5 < by_app[OW]["share"] < 0.65
    assert by_app[CS2]["budget_gb"] + by_app[OW]["budget_gb"] == pytest.approx(8.0, abs=0.02)
    assert by_app[OW]["budget_gb"] > by_app[CS2]["budget_gb"] >= 2.5


def test_one_restart_heavy_day_is_one_vote_not_ten():
    evs = [evening(1, CS2)] * 10 + [evening(2, OW)]
    raw = allocate.shares(allocate.parse_launches(evs), {CS2, OW}, NOW, CFG)
    assert raw[CS2] < 0.6


def test_not_overconfident_runner_up_keeps_a_slice_until_one_app_dominates():
    assert allocate.hedge({CS2: 0.8, OW: 0.2}, CFG) == {CS2: 0.8, OW: 0.2}
    assert allocate.hedge({CS2: 0.9, OW: 0.1}, CFG) == {CS2: 1.0}
    mixed = allocate.hedge({CS2: 0.6, OW: 0.3, "rare.exe": 0.1}, CFG)
    assert set(mixed) == {CS2, OW} and sum(mixed.values()) == pytest.approx(1.0)
    assert allocate.hedge({}, CFG) == {}


def test_not_too_strict_many_small_shares_still_load_the_top_app():
    thin = {f"app{i}.exe": 0.1 for i in range(10)}
    assert len(allocate.hedge(thin, CFG)) == 1


def test_split_never_gives_an_app_more_than_its_set_and_passes_the_rest_on():
    out = allocate.split(8.0, {CS2: 0.5, OW: 0.5}, {CS2: 1.0, OW: 20.0}, CFG)
    assert out == {CS2: 1.0, OW: 7.0}
    assert allocate.split(8.0, {CS2: 0.5, OW: 0.5}, {CS2: 1.0, OW: 2.0}, CFG) == {CS2: 1.0, OW: 2.0}


def test_split_drops_slices_too_small_to_matter_and_gives_them_to_the_rest():
    out = allocate.split(1.0, {CS2: 0.85, OW: 0.15}, {CS2: 12.0, OW: 12.0}, CFG)
    assert out == {CS2: 1.0}
    assert allocate.split(0.1, {CS2: 1.0}, {CS2: 12.0}, CFG) == {}
    assert allocate.split(4.0, {CS2: 1.0}, {CS2: 0.0}, CFG) == {}


def test_quiet_slot_loads_nothing_and_says_so():
    morning = NOW.replace(hour=9, minute=0)        # 11 hours from the evening launches
    plan = allocate.plan(morning, four_week_story(), targets(), 8.0, {}, {})
    assert plan["decision"] == "quiet slot, nothing loaded"
    assert all(r["budget_gb"] == 0 for r in plan["rows"])


def test_empty_history_never_fires():
    plan = allocate.plan(NOW, [], targets(), 8.0, {}, {})
    assert plan["activity"] < CFG["min_activity"] and plan["decision"] != "load"


def test_activity_counts_only_days_the_logger_was_alive_in_the_slot():
    evs = [evening(d, CS2) for d in (1, 2)] + [ev((NOW - timedelta(days=3)).replace(hour=20), "heartbeat")]
    times, launches = allocate.parse_times(evs), allocate.parse_launches(evs)
    busy = allocate.activity(times, launches, {CS2}, NOW, CFG)
    assert CFG["min_activity"] < busy < 0.9      # two of three observed days, weekend days count half on a Monday
    assert allocate.activity(times, launches, {OW}, NOW, CFG) < CFG["min_activity"]


def test_skipped_app_gives_its_share_to_the_other():
    plan = allocate.plan(NOW, four_week_story(), targets(), 8.0, {}, {CS2: "target running"})
    by_app = {r["exe"]: r for r in plan["rows"]}
    assert by_app[CS2]["budget_gb"] == 0 and by_app[CS2]["skip"] == "target running"
    assert by_app[OW]["budget_gb"] == 8.0


def test_measured_gain_and_its_weight():
    evs = [ev(NOW, app=CS2, launch_s=9.0, prefetched=False)] * 4 + [ev(NOW, app=CS2, launch_s=5.0, prefetched=True)] * 5
    gain = allocate.measured_gain(evs, CS2)
    assert (gain["normal_s"], gain["prefetched_s"], gain["saved_s"], gain["prefetched_n"]) == (9.0, 5.0, 4.0, 5)
    assert allocate.gain_weight(gain, CFG) == 1.0
    flat = allocate.measured_gain([ev(NOW, app=OW, launch_s=6.0, prefetched=False)] * 4
                                  + [ev(NOW, app=OW, launch_s=6.0, prefetched=True)] * 5, OW)
    assert allocate.gain_weight(flat, CFG) == CFG["no_gain_weight"]
    few = allocate.measured_gain([ev(NOW, app=OW, launch_s=6.0, prefetched=True)] * 2, OW)
    assert allocate.gain_weight(few, CFG) == 1.0 and few["saved_s"] is None


def test_app_with_no_measured_gain_gets_less_but_not_nothing():
    story = four_week_story()
    flat = [ev(NOW - timedelta(days=40), app=OW, launch_s=6.0, prefetched=False)] * 4 \
        + [ev(NOW - timedelta(days=40), app=OW, launch_s=6.0, prefetched=True)] * 5
    before = {r["exe"]: r["budget_gb"] for r in allocate.plan(NOW, story, targets(), 8.0, {}, {})["rows"]}
    after = {r["exe"]: r["budget_gb"] for r in allocate.plan(NOW, story + flat, targets(), 8.0, {}, {})["rows"]}
    assert 0 < after[OW] < before[OW] and after[CS2] > before[CS2]


def test_replay_split_covers_a_switching_user_better_than_winner_takes_all():
    evs = []
    for d in range(60, 0, -1):                      # switches app every 3 days
        evs.append(evening(d, CS2 if (d // 3) % 2 else OW))
    result = allocate.replay(evs, {CS2, OW}, {})
    assert result["launches"] > 30
    assert result["split_nothing_loaded"] < result["winner_nothing_loaded"]
    assert result["split_nothing_loaded"] <= 0.1


def test_replay_on_a_steady_user_loses_nothing_to_the_hedge():
    evs = [evening(d, CS2) for d in range(40, 0, -1)]
    result = allocate.replay(evs, {CS2, OW}, {})
    assert result["split_mean_cover"] == result["winner_mean_cover"] == 1.0
    assert allocate.replay([], {CS2}, {}) == {"launches": 0}
