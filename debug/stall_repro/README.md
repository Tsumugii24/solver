# Stall Repro

Minimal Linux-only repro helper for boards `46` and `48`.

Defaults:
- board indices: `46,48`
- `OOP_RANGE` and `IP_RANGE` loaded from `ranges/sia.txt`
- simplified postflop tree with one size per `set_bet_sizes`
- `set_raise_limit=1`
- `dump_format=parquet`
- `max_iteration=1`
- `stall_timeout=20`

Run from WSL:

```bash
cd /mnt/d/pocker/solver
python debug/stall_repro/run_min_repro.py
```

Useful variants:

```bash
python debug/stall_repro/run_min_repro.py --indices 46,47,48
python debug/stall_repro/run_min_repro.py --thread-num 1 --use-isomorphism 0
python debug/stall_repro/run_min_repro.py --max-iteration 5 --stall-timeout 60
```




