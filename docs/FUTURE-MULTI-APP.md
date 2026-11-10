# AI Memory Advisor — Future Improvement

## Multi-Application Predictive Memory Management

Status: Proposed for future development. Scaffolding code landed 2026-11-11 (`predictor/multi.py`, `config.json` → `predictor.multi`).
Priority: High

### Objective

Upgrade AI Memory Advisor from a CS2-focused prefetching system into an intelligent, adaptive memory manager capable of predicting and preparing multiple applications without negatively affecting current PC performance.

### Proposed Features

1. **Multi-Application Prediction**
   - Monitor applications such as CS2, Blender, Adobe After Effects, Chrome, and Discord.
   - Learn usage patterns based on time, day of the week, and application history.
   - Calculate independent launch probabilities for each application.
2. **Dynamic Prefetch Budget Allocation**
   - Replace the fixed 24 GB prefetch approach with an adaptive budget based on available RAM.
   - Distribute prefetch capacity among likely applications, considering prediction confidence, useful file sizes, and expected launch-time improvement.
   - Example: Allocate 60% to CS2 and 40% to Blender when justified by their predicted benefits.
3. **Continuous Learning and Prediction Correction**
   - Record actual application launches and compare them with predictions.
   - Learn from incorrect predictions without overreacting to isolated events.
   - Adapt to different weekday, weekend, gaming, and working routines.
4. **Active Application Priority**
   - Immediately pause or cancel unnecessary prefetching when a demanding application launches.
   - Prioritize current applications over predicted future applications.
   - Allow Windows to reclaim cached memory whenever required.
5. **Performance Protection**
   - Monitor RAM availability, CPU usage, and storage activity.
   - Prevent unnecessary prefetching during heavy workloads.
   - Avoid repeated disk reads that provide little measurable benefit.
6. **Performance Benchmarking**
   - Compare cold, warm, and AI-prefetched application launch times.
   - Measure whether incorrect predictions slow unrelated applications.
   - Evaluate performance against Windows' existing caching mechanisms.

### Example Scenario

At 7:50 PM, the AI predicts:
- Saturday: CS2 70%, Blender 20%.
- Monday: CS2 25%, Blender 65%.

The AI selects the most beneficial files to prefetch within its safe memory budget. If the user unexpectedly opens After Effects, the system pauses unnecessary prefetching, prioritizes After Effects, and records the event to improve future predictions.

### Core Design Principle

**Never sacrifice present performance for predicted future performance.**

### Expected Outcome

A personalized Windows memory optimization system that learns application habits, intelligently allocates available cache capacity, reduces application launch times where possible, and minimizes interference with active workloads.

Implementation and performance improvements remain to be developed and validated.

---

## What is already in the code (2026-11-11)

| Feature | Where | Status |
|---|---|---|
| One count-table model per target, independent probabilities | `predictor/multi.py` → `score_all()` | done, scored from the same behaviour log |
| Dynamic budget = fraction of free RAM, capped, split by p × set size | `free_budget_gb()`, `allocate()` | done |
| Per-target threshold rises with EWMA false-fire rate (learning) | `effective_threshold()`, `settle()` | done |
| Pause when a heavy app runs, CPU busy, or disk busy; re-checked before every target | `system_guard()`, `execute()` | done |
| Per-target launch file sets | `config.json` → `predictor.multi.targets[].fileset`, `bench/prefetch.py --fileset` | CS2 recorded; Blender / After Effects / Chrome / Discord = `null`, capture with `bench/capture_fileset.ps1` |
| Events for the dashboard (`mode: "multi"`) | `prefetch_fired`, `prefetch_done`, `prefetch_paused`, `prediction_hit`, `prediction_false` | done |
| Benchmark of interference (does a wrong prefetch slow another app) | — | TODO: `bench/run_bench.py` condition "prefetched-other" |
| Comparison against SysMain alone | — | TODO: SysMain on/off runs |

Run it:
```
python predictor/multi.py --once     # scores + plan, nothing read
python predictor/multi.py --fire     # execute the plan once
```
The single-target daemon (`predictor/daemon.py`) is unchanged; the measured CS2 results stand. Switch over by registering `multi.py` instead of `daemon.py` in the logon task once the extra file sets are captured.
