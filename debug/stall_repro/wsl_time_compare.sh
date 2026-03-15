#!/usr/bin/env bash
set -euo pipefail
cd /mnt/d/pocker/solver
for name in 0046_AcKc3d 0048_AcKc2d; do
  echo "=== ${name} ==="
  /usr/bin/time -v /mnt/d/pocker/solver/install/console_solver -i /mnt/d/pocker/solver/debug/stall_repro/configs/${name}.txt -r /mnt/d/pocker/solver/resources -m holdem > /tmp/${name}.out 2> /tmp/${name}.time
  grep -E "Original TexasSolver heuristic|Calibrated peak estimate|Practical peak estimate|Likely peak while solving|Persistent lower bound" /tmp/${name}.out
  grep "Maximum resident set size" /tmp/${name}.time
  echo
done
