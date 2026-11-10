# Roadmap

Core rule for every item: **never sacrifice present performance for predicted future performance.**

## Done
- Behaviour logger, count-table predictor, user-mode prefetcher (no driver, no admin).
- Measured on CS2 (NVMe): cold 18.6 s, prefetched 14.7 s, warm ceiling 14.6 s. Disk reads during launch down 92 %.
- Measured on Blender 5.2 with a 1.33 GB scene (NVMe): 7.35 s cold, 6.31 s prefetched, 6.03 s warm.
- Standalone app (`advisor/`): 10 learning days, then it works on its own for whatever heavy apps the user has.
  Launch file sets are captured automatically from open and memory-mapped files.

## Next: multi-application memory manager
Design in `docs/FUTURE-MULTI-APP.md`, scaffolding in `predictor/multi.py`.
1. **Budget allocation between apps.** Today the budget is split by probability x launch-set size.
   Replace set size with measured benefit: seconds saved per GB for each app.
2. **Benefit learning.** The standalone app already times every launch (process start to first window)
   with and without a prefetch. Feed that back so apps that gain nothing stop being prefetched.
3. **Partial sets.** When the budget is short, read the files seen in the most launches first
   (already ordered that way) and measure how much of the gain the first N GB gives.
4. **Interference benchmark.** Does a wrong prefetch slow an unrelated app? Condition "prefetched-other".
5. **Against Windows itself.** SysMain on and off, same launches.
6. **Better prediction.** The count table is weak on real data. Try sequence features (last 3 apps),
   session length, and day-type clusters. Report precision and recall honestly.

## Shared data pool (opt-in)
People without a routine are where the count table fails. `AIMemoryAdvisor.exe --share` writes an
anonymised export (`docs/SHARING.md`); nothing is uploaded.
- A private upload channel with a clear consent text, so files do not have to go through public issues.
- A public, anonymised data set once enough people have shared.
- Models trained on the pool, shipped as a starting point so day 1 is not empty (cold start).

## Slow storage
Plan in `docs/PLAN-SLOW-STORAGE.md`. The gain should be largest on HDD and SATA SSD.
- Full HDD benchmark with a heavy scene, 10 runs per condition.
- Prefetch cost on HDD is long (tens of seconds). The prediction must fire earlier, so the
  lead time becomes part of the model.

## Product
- Tray icon and a pause button.
- Signed build, so SmartScreen stops warning.
- Per-app opt-out list in the config.
- Linux and macOS: same idea (`posix_fadvise`, `madvise`), not started.

## Contributing
Open an issue first for anything bigger than a bug fix. Bench results need the raw rows and
the method, including how the cold condition was produced.
