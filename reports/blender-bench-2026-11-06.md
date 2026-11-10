# Blender 5.2 launch benchmark, no-admin method (6/11/2026)

Subject: Blender 5.2 opening `Coat_Render.blend` (1.33 GB). 5 rounds x 3 conditions, round-robin, 10:47-10:59.
Runner: `bench/run_bench_user.py all --app blender --runs 5`. Raw rows: `data/bench/launches.jsonl`, labels `user-blender-*`.

| Condition | What happens first | io_settled median (s) | mean (s) | window median (s) | disk read median (MB) |
|---|---|---|---|---|---|
| cold | RAM-pressure flush, nothing prefetched | **7.35** | 7.76 | 0.72 | 1,762 |
| full | flush, then advisor prefetch of the file set (196 files, 2.09 GB) | **6.31** | 6.32 | 0.69 | 264 |
| warm | nothing, right after a launch (ceiling) | **6.03** | 5.97 | 0.67 | 52 |

- Advisor saves **1.04 s (14 %)** vs cold, and closes **79 %** of the cold-to-warm gap.
- Disk reads during launch drop from ~1.76 GB to ~0.26 GB (-85 %).
- Spread: cold 6.66-10.73 s (one slow outlier in round 2), full 6.29-6.37 s, warm 5.73-6.05 s. Prefetch also makes the launch more predictable.
- Window appears in ~0.7 s in every condition. The gain is in loading the scene, not in showing the window.

## Method notes (put in the thesis)
- Cold flush without admin: allocate ~3 GB past available RAM with incompressible (random) filler, then free it. Verified: after the flush the 1.33 GB .blend was re-read entirely from disk (1.33 GB disk read, vs 0 when warm). A flush with identical filler pages did not work because Windows memory compression squeezed them.
- File set without ProcMon: files Blender has open or memory-mapped during one launch, plus the .blend, read whole (no byte ranges).
- io_settled = disk reads settled (same definition as the CS2 bench).

## Caveats
- NVMe and 34 GB RAM: the cold penalty is small (about 1.3 s) because the drive is fast. On a SATA SSD or HDD the gap would be larger. This is a test to run next.
- n = 5 per condition. The cold outlier pulls the mean; medians are reported.
- A browser was in use on the same PC during rounds 1-3, which may add noise.
