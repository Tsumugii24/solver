#!/usr/bin/env python3
"""Benchmark single action-line + hand queries across export formats."""

from __future__ import annotations

import argparse
import io
import statistics
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from query_action_line import ActionLineQuery  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark the common workflow of querying one fixed action line "
            "for one fixed hand across json, parquet, and parquet_native exports."
        )
    )
    parser.add_argument("--config", type=Path, required=True, help="Solver config used to generate the exports")
    parser.add_argument("--json", dest="json_path", type=Path, required=True, help="Baseline JSON export")
    parser.add_argument(
        "--parquet-json",
        dest="parquet_path",
        type=Path,
        required=True,
        help="Opaque parquet export",
    )
    parser.add_argument(
        "--parquet-structured",
        dest="parquet_native_path",
        type=Path,
        required=True,
        help="Native parquet_native export",
    )
    parser.add_argument("--action-line", required=True, help="Action line, e.g. 'ROOT -> CHECK -> BET 10 -> RAISE 100'")
    parser.add_argument("--hand", required=True, help="Hand to query, e.g. 'KhQc'")
    parser.add_argument(
        "--cold-repeats",
        type=int,
        default=20,
        help="How many script-like fresh-load queries to benchmark per format",
    )
    parser.add_argument(
        "--warm-repeats",
        type=int,
        default=200,
        help="How many in-memory repeat queries to benchmark per format",
    )
    return parser.parse_args()


def quiet_call(callback) -> None:
    """Run a callback while suppressing stdout noise from existing scripts."""
    with redirect_stdout(io.StringIO()):
        callback()


def main() -> int:
    """Program entrypoint."""
    args = parse_args()
    files = {
        "json": args.json_path,
        "parquet": args.parquet_path,
        "parquet_native": args.parquet_native_path,
    }

    for name, path in files.items():
        cold_total: list[float] = []
        cold_init: list[float] = []
        cold_load: list[float] = []
        cold_query: list[float] = []
        warm_query: list[float] = []

        for _ in range(args.cold_repeats):
            start = time.perf_counter()
            with redirect_stdout(io.StringIO()):
                query = ActionLineQuery(str(path), str(args.config))
            after_init = time.perf_counter()
            quiet_call(query.load)
            after_load = time.perf_counter()
            quiet_call(lambda: query.query_hand(args.action_line, args.hand))
            after_query = time.perf_counter()

            cold_total.append(after_query - start)
            cold_init.append(after_init - start)
            cold_load.append(after_load - after_init)
            cold_query.append(after_query - after_load)

        with redirect_stdout(io.StringIO()):
            query = ActionLineQuery(str(path), str(args.config))
        quiet_call(query.load)

        for _ in range(args.warm_repeats):
            start = time.perf_counter()
            quiet_call(lambda: query.query_hand(args.action_line, args.hand))
            warm_query.append(time.perf_counter() - start)

        print(f"[{name}]")
        print(f"  size_bytes={path.stat().st_size}")
        print(f"  cold_total_ms_avg={statistics.mean(cold_total) * 1000:.3f}")
        print(f"  cold_init_ms_avg={statistics.mean(cold_init) * 1000:.3f}")
        print(f"  cold_load_ms_avg={statistics.mean(cold_load) * 1000:.3f}")
        print(f"  cold_query_ms_avg={statistics.mean(cold_query) * 1000:.3f}")
        print(f"  warm_query_ms_avg={statistics.mean(warm_query) * 1000:.3f}")
        print(f"  warm_query_ms_min={min(warm_query) * 1000:.3f}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
