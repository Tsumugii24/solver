#!/usr/bin/env python3
"""Benchmark full export loading and all-action-node query traversal."""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from query_action_line import ActionLineQuery  # noqa: E402
from solver_result_io import load_solver_result  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark solver result loading and traversal-query speed across "
            "json, parquet, and parquet_native exports."
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
    parser.add_argument(
        "--load-repeats",
        type=int,
        default=10,
        help="How many times to benchmark bare load_solver_result() per format",
    )
    parser.add_argument(
        "--query-repeats",
        type=int,
        default=5,
        help="How many times to benchmark ActionLineQuery.load() + all-path traversal per format",
    )
    return parser.parse_args()


def collect_action_paths(root: dict[str, Any]) -> list[list[str]]:
    """Collect every action-node path in breadth-first order."""
    paths: list[list[str]] = []
    queue: deque[tuple[dict[str, Any], list[str]]] = deque([(root, [])])
    while queue:
        node, path = queue.popleft()
        node_type = node.get("node_type")
        if node_type == "action_node":
            paths.append(path)
            for action, child in node.get("childrens", {}).items():
                queue.append((child, path + [action]))
        elif node_type == "chance_node":
            for card, child in node.get("dealcards", {}).items():
                queue.append((child, path + [f"DEAL:{card}"]))
    return paths


def benchmark_load(path: Path, repeats: int) -> list[float]:
    """Benchmark bare load_solver_result()."""
    times: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        load_solver_result(path)
        times.append(time.perf_counter() - start)
    return times


def benchmark_query(path: Path, config: Path, paths: list[list[str]], repeats: int) -> tuple[list[float], list[float], int]:
    """Benchmark ActionLineQuery.load() plus traversing all action-node paths."""
    load_times: list[float] = []
    query_times: list[float] = []
    rows_extracted = 0
    for _ in range(repeats):
        query = ActionLineQuery(str(path), str(config))
        start = time.perf_counter()
        query.load()
        load_times.append(time.perf_counter() - start)

        start = time.perf_counter()
        total_rows = 0
        for action_path in paths:
            result = query._navigate_to_node(action_path)
            if result is None:
                raise RuntimeError(f"Missing path while benchmarking: {action_path}")
            node, actual_path, ip_range, oop_range = result
            total_rows += len(query._extract_node_data(node, actual_path, ip_range, oop_range))
        query_times.append(time.perf_counter() - start)
        rows_extracted = total_rows
    return load_times, query_times, rows_extracted


def print_stat(label: str, values: list[float]) -> None:
    """Print ms-based summary stats."""
    print(f"  {label}_avg_ms={statistics.mean(values) * 1000:.3f}")
    print(f"  {label}_min_ms={min(values) * 1000:.3f}")


def main() -> int:
    """Program entrypoint."""
    args = parse_args()
    files = {
        "json": args.json_path,
        "parquet": args.parquet_path,
        "parquet_native": args.parquet_native_path,
    }

    baseline = load_solver_result(args.json_path)
    action_paths = collect_action_paths(baseline)

    print(f"action_paths={len(action_paths)}")
    print()
    for name, path in files.items():
        load_only = benchmark_load(path, args.load_repeats)
        actionline_load, query_all_paths, rows_extracted = benchmark_query(
            path, args.config, action_paths, args.query_repeats
        )
        print(f"[{name}]")
        print(f"  size_bytes={path.stat().st_size}")
        print_stat("load_only", load_only)
        print_stat("actionline_load", actionline_load)
        print_stat("query_all_paths", query_all_paths)
        print(f"  rows_extracted={rows_extracted}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
