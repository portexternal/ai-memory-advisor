# Experiment plan

Deadline: 3 October 2026. Team: 1 (as of 3 Sep 2026). Nothing built before 3 Sep 2026.

## Claim to prove

A per-user next-app predictor plus a user-mode prefetcher makes CS2 reach its main menu
faster than stock Windows, and captures a measurable fraction of the app-level OPT (oracle) gain.

## Test machine (this PC)

- MSI MS-7D99, 32 GB RAM, RTX 4060
- WD_BLACK SN770 1 TB NVMe (CS2 lives here now)
- WDC WD10EZEX 1 TB HDD (SATA) — second Steam library for the "slow disk" condition
- Windows 11, SysMain running

## Conditions (three bars per chart)

1. **Stock**: SysMain state as chosen, no prefetcher.
2. **Ours**: predictor fires prefetch of CS2 launch file set N minutes before predicted launch.
3. **OPT / oracle**: prefetch always fired at the right time (upper bound). App-level only.

Primary run with SysMain **off** in all conditions (clean). Secondary run with SysMain **on**.

## Metrics

- Launch latency: `cs2.exe` process start (ETW / psutil create_time) → main menu visible.
- Page faults / hard faults during launch (perf counters or ETW).
- SSD/HDD bytes read during launch.
- Prefetcher CPU time and RAM budget used.
- Prediction accuracy from trace replay (precision/recall of "CS2 within next N min").
- Repeats: 10 per condition, report mean ± std.

## Cold start procedure

RAMMap (Sysinternals) `-Es -Ew` as admin, then wait 10 s. Validate against reboot runs.

## Launch file set

Process Monitor: filter `cs2.exe` file reads from process start to main menu. Save path list.
Prefetcher reads exactly that list (sequential `ReadFile`, or `PrefetchVirtualMemory` on mapped files).

## Behavior data

Logger runs from 3 Sep 2026 on this PC. Scripted "human" routine with ~20% noise
(CS2 / Spotify / Chrome / Discord) so the model does not trivially hit 100%.
Features: hour bucket, weekday, previous app, idle-to-active transition, weather (logged, expected weak in 2 weeks).
Model: count table / naive Bayes or small decision tree. Fire when P(CS2 within N min) > threshold.

## Weeks

| Week | Goal |
|------|------|
| 1 (3–9 Sep) | Logger live. Cold vs warm CS2 timing by hand = ceiling number. Prefetcher reads file list into cache. |
| 2 (10–16 Sep) | Procmon file set. RAMMap flush script. Automated launch timer. Model on synthetic data. |
| 3 (17–23 Sep) | Train on real log. 3 conditions × 10 launches, NVMe and HDD. |
| 4 (24 Sep–3 Oct) | Oracle computation, charts, cross-device export/import demo, writing. |

## Risks

- NVMe cold/warm gap too small to show. Mitigation: HDD library condition.
- SysMain learns CS2 over 2 weeks and moves the baseline. Mitigation: SysMain-off primary run.
- Lead time: prefetch of a few GB needs seconds (NVMe) to minutes (HDD). Time-of-day features give that lead.
- Two weeks = 2 Saturdays. Weather feature will not be statistically supported. Say so.
