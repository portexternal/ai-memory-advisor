# Slow-storage experiment (professor's suggestion, 11/11/2026)

Feedback: "It would be more useful on cloud or an external drive, and you would see the prefetch really work, in more seconds."
He is right. On the thesis PC the game sits on a WD_BLACK SN770 NVMe (2.1 GB/s), so the whole disk-bound part of a cold launch is only 3.8 s. Everything else (14.6 s) is CPU and shader work the prefetcher cannot touch. Move the same 13.5 GB launch set to slower storage and the disk-bound part grows while the prefetched launch stays at the warm floor.

## Estimates (same 13.5 GB launch read, same warm floor 14.6 s)
Overlap factor 0.62 = (18.6 - 14.6) / (13.5 GB / 2.1 GB/s), i.e. how much of the read time shows up in the launch on NVMe. Applied to slower drives; measure to confirm.

| Storage | Bandwidth | Read time | Est. cold launch | Prefetched | Saved | Prefetch must start |
|---|---|---|---|---|---|---|
| NVMe SN770 (measured) | 2.1 GB/s | 6 s | 18.6 s | 14.6 s | 4 s | 6 s ahead |
| SATA SSD | 500 MB/s | 27 s | ~31 s | 14.6 s | ~17 s | 30 s ahead |
| USB 3 external SSD | 350 MB/s | 39 s | ~39 s | 14.6 s | ~24 s | 40 s ahead |
| USB 3 external HDD | 120 MB/s | 112 s | ~85 s | 14.6 s | ~70 s | 2 min ahead |
| Gigabit LAN share / NAS | 110 MB/s | 123 s | ~91 s | 14.6 s | ~76 s | 2 min ahead |
| Wi-Fi NAS / cloud-synced folder | 50 MB/s | 270 s | ~183 s | 14.6 s | ~2.8 min | 5 min ahead |
| USB 2 / cheap flash | 35 MB/s | 386 s | ~255 s | 14.6 s | ~4 min | 7 min ahead |

Reading: the prediction window (30 min) already covers every row. The fileset must fit the RAM budget (12.4 GB holds 12.3 GB).

## How to run it (no new code needed)
1. Copy or install CS2 to the external drive (or a network share mapped as a drive letter). Steam: Settings → Storage → add library on that drive → Move.
2. Capture a fresh file set on that path: `python bench/run_bench.py capture` (paths now point at E:\... instead of C:\). This writes `data/fileset/cs2_launch.json`; keep the NVMe one as a backup (run_bench already backs up with a timestamp).
3. Cold runs: `python bench/run_bench.py cold` x 10. Prefetched: `python bench/run_bench.py full` x 10. Warm: `warm` x 10. Label the drive with `launch_timer.py --disk usb-hdd` (the `--disk` argument already exists).
4. Record bytes read from the external volume with `psutil.disk_io_counters(perdisk=True)` before and after each launch (add the volume to the bench log line).
5. Chart: three bars per storage type. Expect the grey cold bar to grow with slower storage, amber and green to stay at ~14.6 s.

## Caveats to say out loud
- External drives on Windows default to "quick removal" (write cache off). Read caching through the standby list still works, but verify with the warm run first: if warm on the external drive is also slow, the cache is not holding the pages.
- Cloud "files on demand" (OneDrive, Google Drive): a placeholder file must be hydrated on first read. Prefetching = hydrating early. After that the file is local until dehydrated again, so the second cold run is really a local run. Use a mapped network share for a clean cloud-like test.
- Prefetch time grows with the slow drive. The guard "do not fire while the target runs" matters more: a 2-minute prefetch overlapping a launch competes for the same slow disk.
- Gains above are extrapolations until measured. Present them as the hypothesis, then the measured bars.
