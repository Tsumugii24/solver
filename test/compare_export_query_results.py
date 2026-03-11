#!/usr/bin/env python3
"""Compare queried node payloads across JSON and Parquet solver exports."""

from __future__ import annotations

import argparse
import math
from collections import deque
from pathlib import Path
from typing import Any

import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from query_action_line import ActionLineQuery  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Compare ActionLineQuery results across JSON, parquet, and "
            "parquet_native exports."
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
        "--action-line",
        action="append",
        default=[],
        help="Specific action line to compare, e.g. 'ROOT -> CHECK -> BET 10'. Can be repeated.",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=1e-5,
        help="Numeric comparison tolerance for floating-point values",
    )
    return parser.parse_args()


def collect_all_action_paths(root: dict[str, Any]) -> list[list[str]]:
    """Return every action-node path in breadth-first order."""
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


def parse_action_line(action_line: str) -> list[str]:
    """Convert ROOT -> ... action line text into token list."""
    if "->" in action_line:
        actions = [part.strip() for part in action_line.split("->")]
    else:
        actions = [part.strip() for part in action_line.split(",")]
    return [action for action in actions if action and action.upper() != "ROOT"]


def format_path(path: list[str]) -> str:
    """Render a path for human-readable output."""
    return "ROOT" if not path else "ROOT -> " + " -> ".join(path)


def approx_equal(lhs: Any, rhs: Any, tolerance: float) -> bool:
    """Recursively compare nested structures with numeric tolerance."""
    if isinstance(lhs, (int, float)) and isinstance(rhs, (int, float)):
        return math.isclose(float(lhs), float(rhs), rel_tol=tolerance, abs_tol=tolerance)
    if type(lhs) is not type(rhs):
        return False
    if isinstance(lhs, list):
        return len(lhs) == len(rhs) and all(approx_equal(a, b, tolerance) for a, b in zip(lhs, rhs))
    if isinstance(lhs, dict):
        return set(lhs.keys()) == set(rhs.keys()) and all(
            approx_equal(lhs[key], rhs[key], tolerance) for key in lhs
        )
    return lhs == rhs


def max_abs_diff(lhs: Any, rhs: Any) -> float:
    """Return the maximum absolute numeric difference inside nested structures."""
    if isinstance(lhs, (int, float)) and isinstance(rhs, (int, float)):
        return abs(float(lhs) - float(rhs))
    if isinstance(lhs, list) and isinstance(rhs, list):
        return max((max_abs_diff(a, b) for a, b in zip(lhs, rhs)), default=0.0)
    if isinstance(lhs, dict) and isinstance(rhs, dict):
        return max((max_abs_diff(lhs[key], rhs[key]) for key in lhs.keys() & rhs.keys()), default=0.0)
    return 0.0


def canonicalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort extracted rows to make comparisons deterministic."""
    normalized = []
    for row in rows:
        normalized.append(
            {
                "path": row["path"],
                "player": row["player"],
                "hand": row["hand"],
                "actions": row["actions"],
                "probs": row["probs"],
                "action_probs": row["action_probs"],
                "ip_range": row["ip_range"],
                "oop_range": row["oop_range"],
                "ev": row["ev"],
                "equity": row["equity"],
            }
        )
    normalized.sort(key=lambda item: item["hand"])
    return normalized


def rows_for_path(query: ActionLineQuery, path: list[str]) -> list[dict[str, Any]]:
    """Extract comparable query rows for a specific action-node path."""
    result = query._navigate_to_node(path)
    if result is None:
        raise RuntimeError(f"Missing path: {format_path(path)}")
    node, actual_path, ip_range, oop_range = result
    rows = query._extract_node_data(node, actual_path, ip_range, oop_range)
    return canonicalize_rows(rows)


def main() -> int:
    """Program entrypoint."""
    args = parse_args()
    queries = {
        "json": ActionLineQuery(str(args.json_path), str(args.config)),
        "parquet": ActionLineQuery(str(args.parquet_path), str(args.config)),
        "parquet_native": ActionLineQuery(str(args.parquet_native_path), str(args.config)),
    }
    for query in queries.values():
        query.load()

    if args.action_line:
        paths = [parse_action_line(action_line) for action_line in args.action_line]
    else:
        paths = collect_all_action_paths(queries["json"].data)

    print(f"Comparing {len(paths)} action-node path(s)")
    print(f"Tolerance: {args.tolerance}")

    exact_mismatch_count = 0
    approx_mismatch_count = 0
    max_numeric_delta = 0.0
    mismatch_messages: list[str] = []

    for path in paths:
        baseline_rows = rows_for_path(queries["json"], path)
        for other_name in ("parquet", "parquet_native"):
            candidate_rows = rows_for_path(queries[other_name], path)
            if baseline_rows != candidate_rows:
                exact_mismatch_count += 1
            if len(baseline_rows) != len(candidate_rows):
                approx_mismatch_count += 1
                mismatch_messages.append(
                    f"{other_name} @ {format_path(path)}: row count differs "
                    f"({len(baseline_rows)} vs {len(candidate_rows)})"
                )
                continue
            for baseline_row, candidate_row in zip(baseline_rows, candidate_rows):
                if baseline_row["hand"] != candidate_row["hand"]:
                    approx_mismatch_count += 1
                    mismatch_messages.append(
                        f"{other_name} @ {format_path(path)}: hand differs "
                        f"({baseline_row['hand']} vs {candidate_row['hand']})"
                    )
                    break
                for key in ("actions", "probs", "action_probs", "ip_range", "oop_range", "ev", "equity"):
                    max_numeric_delta = max(max_numeric_delta, max_abs_diff(baseline_row[key], candidate_row[key]))
                    if not approx_equal(baseline_row[key], candidate_row[key], args.tolerance):
                        approx_mismatch_count += 1
                        mismatch_messages.append(
                            f"{other_name} @ {format_path(path)} hand {baseline_row['hand']}: key '{key}' differs"
                        )
                        break
                else:
                    continue
                break

    print(f"Exact mismatch count: {exact_mismatch_count}")
    print(f"Approx mismatch count: {approx_mismatch_count}")
    print(f"Max absolute numeric diff: {max_numeric_delta:.10f}")

    if mismatch_messages:
        print("Mismatches:")
        for message in mismatch_messages[:20]:
            print(f"- {message}")
        if len(mismatch_messages) > 20:
            print(f"... and {len(mismatch_messages) - 20} more")
        return 1

    print("All queried payloads match within tolerance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
