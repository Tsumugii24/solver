#!/usr/bin/env bash
set -euo pipefail
cd /mnt/d/pocker/solver
/usr/bin/time -v /mnt/d/pocker/solver/install/console_solver -i /mnt/d/pocker/solver/debug/stall_repro/configs/0048_AcKc2d.txt -r /mnt/d/pocker/solver/resources -m holdem > /tmp/0048_rl3.out 2> /tmp/0048_rl3.time
grep -E 'Original TexasSolver heuristic|Calibrated peak estimate|Practical peak estimate|Likely peak while solving|Persistent lower bound' /tmp/0048_rl3.out
grep 'Maximum resident set size' /tmp/0048_rl3.time
