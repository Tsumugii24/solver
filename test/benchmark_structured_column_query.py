#!/usr/bin/env python3
"""Benchmark direct columnar querying for structured Parquet exports."""

from __future__ import annotations

import argparse
import io
import statistics
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

import pyarrow.compute as pc
import pyarrow.parquet as pq

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from query_action_line import ActionLineQuery  # noqa: E402

TRAVERSAL_COLUMNS = ["node_id", "parent_node_id", "edge_label"]
PAYLOAD_COLUMNS = [
    "node_id",
    "hand",
    "strategy_probs",
    "evs",
    "equities",
    "ip_range",
    "oop_range",
    "actions",
    "player",
]
ALL_COLUMNS = list(dict.fromkeys(TRAVERSAL_COLUMNS + PAYLOAD_COLUMNS))


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark direct node_id + column-filter querying against the current "
            "ActionLineQuery structured-Parquet path."
        )
    )
    parser.add_argument("--config", type=Path, required=True, help="Solver config used to generate the export")
    parser.add_argument(
        "--parquet-structured",
        dest="parquet_native_path",
        type=Path,
        required=True,
        help="Native parquet_native export",
    )
    parser.add_argument("--action-line", required=True, help="Action line, e.g. 'ROOT -> CHECK -> BET 10 -> RAISE 100'")
    parser.add_argument("--hand", required=True, help="Hand to query, e.g. 'KhQc'")
    parser.add_argument("--cold-repeats", type=int, default=30, help="How many fresh-load runs to benchmark")
    parser.add_argument("--warm-repeats", type=int, default=300, help="How many repeated in-memory queries to benchmark")
    return parser.parse_args()


def parse_action_line(action_line: str) -> list[str]:
    """Convert action line text into token list."""
    if "->" in action_line:
        parts = [part.strip() for part in action_line.split("->")]
    else:
        parts = [part.strip() for part in action_line.split(",")]
    return [part for part in parts if part and part.upper() != "ROOT"]


class StructuredQueryCached:
    """Load only needed columns once, then resolve node ids in-memory."""

    def __init__(self, parquet_path: Path):
        self.parquet_path = parquet_path
        self.table = None
        self.root_id = None

    def load(self) -> None:
        """Load minimal columns needed for traversal + payload extraction."""
        self.table = pq.read_table(self.parquet_path, columns=ALL_COLUMNS)
        self.root_id = self.table.filter(pc.is_null(self.table["parent_node_id"])).column("node_id").to_pylist()[0]

    def _child_node_id(self, parent_id: int, edge_label: str) -> int:
        filt = pc.and_(pc.equal(self.table["parent_node_id"], parent_id), pc.equal(self.table["edge_label"], edge_label))
        values = self.table.filter(filt).column("node_id").to_pylist()
        if not values:
            raise KeyError((parent_id, edge_label))
        return int(values[0])

    def resolve_path(self, actions: list[str]) -> int:
        """Resolve an action line into its target node_id."""
        current = self.root_id
        for action in actions:
            current = self._child_node_id(current, action)
        return current

    def query_hand(self, actions: list[str], hand: str) -> int:
        """Filter the in-memory table down to one node/hand row."""
        node_id = self.resolve_path(actions)
        filt = pc.and_(pc.equal(self.table["node_id"], node_id), pc.equal(self.table["hand"], hand))
        result = self.table.filter(filt)
        return result.num_rows


class StructuredQueryFilteredReads:
    """Resolve paths and payloads via repeated Parquet filter reads."""

    def __init__(self, parquet_path: Path):
        self.parquet_path = parquet_path
        self.root_id = None

    def load(self) -> None:
        """Load only enough to determine the root node id."""
        table = pq.read_table(self.parquet_path, columns=["node_id", "parent_node_id"])
        self.root_id = table.filter(pc.is_null(table["parent_node_id"])).column("node_id").to_pylist()[0]

    def _child_node_id(self, parent_id: int, edge_label: str) -> int:
        table = pq.read_table(
            self.parquet_path,
            columns=["node_id"],
            filters=[("parent_node_id", "=", parent_id), ("edge_label", "=", edge_label)],
        )
        values = table.column("node_id").to_pylist()
        if not values:
            raise KeyError((parent_id, edge_label))
        return int(values[0])

    def resolve_path(self, actions: list[str]) -> int:
        """Resolve an action line into its target node_id."""
        current = self.root_id
        for action in actions:
            current = self._child_node_id(current, action)
        return current

    def query_hand(self, actions: list[str], hand: str) -> int:
        """Run filtered Parquet reads to locate one node/hand row."""
        node_id = self.resolve_path(actions)
        table = pq.read_table(
            self.parquet_path,
            columns=PAYLOAD_COLUMNS,
            filters=[("node_id", "=", node_id), ("hand", "=", hand)],
        )
        return table.num_rows


def benchmark_strategy(name: str, builder, cold_repeats: int, warm_repeats: int) -> dict[str, float]:
    """Benchmark one strategy object factory."""
    cold_total = []
    cold_load = []
    cold_query = []
    warm_query = []

    for _ in range(cold_repeats):
        start = time.perf_counter()
        strategy, actions, hand = builder()
        strategy.load()
        after_load = time.perf_counter()
        strategy.query_hand(actions, hand)
        after_query = time.perf_counter()
        cold_total.append(after_query - start)
        cold_load.append(after_load - start)
        cold_query.append(after_query - after_load)

    strategy, actions, hand = builder()
    strategy.load()
    for _ in range(warm_repeats):
        start = time.perf_counter()
        strategy.query_hand(actions, hand)
        warm_query.append(time.perf_counter() - start)

    return {
        "cold_total_ms_avg": statistics.mean(cold_total) * 1000,
        "cold_load_ms_avg": statistics.mean(cold_load) * 1000,
        "cold_query_ms_avg": statistics.mean(cold_query) * 1000,
        "warm_query_ms_avg": statistics.mean(warm_query) * 1000,
        "warm_query_ms_min": min(warm_query) * 1000,
    }


def benchmark_action_line_query(
    parquet_path: Path,
    config_path: Path,
    action_line: str,
    hand: str,
    cold_repeats: int,
    warm_repeats: int,
) -> dict[str, float]:
    """Benchmark the existing tree-reconstruction path for comparison."""
    cold_total = []
    cold_load = []
    cold_query = []
    warm_query = []

    for _ in range(cold_repeats):
        start = time.perf_counter()
        with redirect_stdout(io.StringIO()):
            query = ActionLineQuery(str(parquet_path), str(config_path))
            query.load()
        after_load = time.perf_counter()
        with redirect_stdout(io.StringIO()):
            query.query_hand(action_line, hand)
        after_query = time.perf_counter()
        cold_total.append(after_query - start)
        cold_load.append(after_load - start)
        cold_query.append(after_query - after_load)

    with redirect_stdout(io.StringIO()):
        query = ActionLineQuery(str(parquet_path), str(config_path))
        query.load()
    for _ in range(warm_repeats):
        start = time.perf_counter()
        with redirect_stdout(io.StringIO()):
            query.query_hand(action_line, hand)
        warm_query.append(time.perf_counter() - start)

    return {
        "cold_total_ms_avg": statistics.mean(cold_total) * 1000,
        "cold_load_ms_avg": statistics.mean(cold_load) * 1000,
        "cold_query_ms_avg": statistics.mean(cold_query) * 1000,
        "warm_query_ms_avg": statistics.mean(warm_query) * 1000,
        "warm_query_ms_min": min(warm_query) * 1000,
    }


def print_stats(name: str, stats: dict[str, float]) -> None:
    """Pretty-print benchmark results."""
    print(f"[{name}]")
    for key, value in stats.items():
        print(f"  {key}={value:.3f}")
    print()


def main() -> int:
    """Program entrypoint."""
    args = parse_args()
    actions = parse_action_line(args.action_line)

    def build_cached():
        return StructuredQueryCached(args.parquet_native_path), actions, args.hand

    def build_filtered_reads():
        return StructuredQueryFilteredReads(args.parquet_native_path), actions, args.hand

    # Verify both prototype strategies can locate the target row.
    for strategy_cls in (StructuredQueryCached, StructuredQueryFilteredReads):
        strategy = strategy_cls(args.parquet_native_path)
        strategy.load()
        count = strategy.query_hand(actions, args.hand)
        if count != 1:
            raise RuntimeError(f"{strategy_cls.__name__} expected 1 row, got {count}")

    print(f"structured_size_bytes={args.parquet_native_path.stat().st_size}")
    print(f"action_line={args.action_line}")
    print(f"hand={args.hand}")
    print()

    print_stats(
        "ActionLineQuery structured",
        benchmark_action_line_query(
            args.parquet_native_path,
            args.config,
            args.action_line,
            args.hand,
            args.cold_repeats,
            args.warm_repeats,
        ),
    )
    print_stats(
        "StructuredQueryCached",
        benchmark_strategy("StructuredQueryCached", build_cached, args.cold_repeats, args.warm_repeats),
    )
    print_stats(
        "StructuredQueryFilteredReads",
        benchmark_strategy("StructuredQueryFilteredReads", build_filtered_reads, args.cold_repeats, args.warm_repeats),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
