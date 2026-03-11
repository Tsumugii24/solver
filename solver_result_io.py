"""
Shared loaders for solver JSON / Parquet outputs.

Supported formats:
- JSON export
- Opaque Parquet with a single `data` column containing JSON text
- Structured Parquet emitted by the native C++ exporter
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable

try:
    import pyarrow.parquet as pq
except ImportError:
    pq = None  # type: ignore


STRUCTURED_REQUIRED_COLUMNS = {"node_id", "node_type", "depth", "reach_probs_only"}


def _require_pyarrow() -> None:
    if pq is None:
        raise RuntimeError("Please install pyarrow: pip install pyarrow")


def _normalize_export_format_name(value: str) -> str:
    if value in {"parquet", "parquet_json"}:
        return "parquet"
    if value in {"parquet_native", "parquet_structured"}:
        return "parquet_native"
    return value


def detect_parquet_format(parquet_path: Path) -> str:
    """Return `parquet` or `parquet_native`."""
    _require_pyarrow()
    parquet_path = Path(parquet_path)
    metadata = pq.read_metadata(parquet_path)
    key_value_metadata = metadata.metadata or {}
    export_format_raw = key_value_metadata.get(b"solver_export_format")
    if export_format_raw is not None:
        export_format = _normalize_export_format_name(export_format_raw.decode("utf-8"))
        if export_format in {"parquet", "parquet_native"}:
            return export_format

    schema_names = set(metadata.schema.names)
    if "data" in schema_names:
        return "parquet"
    if STRUCTURED_REQUIRED_COLUMNS.issubset(schema_names):
        return "parquet_native"
    raise ValueError(f"Unsupported Parquet schema: {parquet_path}")


def load_solver_result(path: str | Path) -> Dict[str, Any]:
    """Load any supported solver result into the legacy JSON tree shape."""
    path = Path(path)
    if path.suffix.lower() == ".parquet":
        return load_solver_result_from_parquet(path)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_solver_result_from_parquet(parquet_path: str | Path) -> Dict[str, Any]:
    """Load solver result from either opaque or structured Parquet."""
    _require_pyarrow()
    parquet_path = Path(parquet_path)
    export_format = detect_parquet_format(parquet_path)
    table = pq.read_table(parquet_path)
    records = table.to_pylist()
    if not records:
        raise ValueError(f"Parquet file is empty: {parquet_path}")

    if export_format == "parquet":
        raw = records[0].get("data")
        return json.loads(raw) if isinstance(raw, str) else raw
    return _reconstruct_structured_tree(records)


def _reconstruct_structured_tree(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    grouped: dict[int, list[Dict[str, Any]]] = defaultdict(list)
    parent_of: dict[int, int | None] = {}
    edge_of: dict[int, str | None] = {}

    for row in records:
        node_id = int(row["node_id"])
        grouped[node_id].append(row)
        parent_value = row.get("parent_node_id")
        parent_of.setdefault(node_id, None if parent_value is None else int(parent_value))
        edge_of.setdefault(node_id, row.get("edge_label"))

    if not grouped:
        raise ValueError("Structured Parquet file has no rows")

    nodes: dict[int, Dict[str, Any]] = {}
    for node_id, rows in grouped.items():
        first = rows[0]
        node_type = first.get("node_type")
        if node_type == "action_node":
            nodes[node_id] = _reconstruct_action_node(rows)
        elif node_type == "chance_node":
            node = {"node_type": "chance_node"}
            if first.get("deal_number") is not None:
                node["deal_number"] = first["deal_number"]
            nodes[node_id] = node
        else:
            raise ValueError(f"Unsupported node_type in structured parquet: {node_type}")

    root_id: int | None = None
    for node_id in sorted(grouped):
        parent_id = parent_of.get(node_id)
        if parent_id is None:
            if root_id is None:
                root_id = node_id
            continue

        edge_label = edge_of.get(node_id)
        if not edge_label:
            raise ValueError(f"Structured Parquet row missing edge_label for node {node_id}")

        parent = nodes[parent_id]
        if parent.get("node_type") == "chance_node":
            parent.setdefault("dealcards", {})[edge_label] = nodes[node_id]
        else:
            parent.setdefault("childrens", {})[edge_label] = nodes[node_id]

    if root_id is None:
        raise ValueError("Structured Parquet tree is missing a root node")
    return nodes[root_id]


def _reconstruct_action_node(rows: list[Dict[str, Any]]) -> Dict[str, Any]:
    first = rows[0]
    actions = first.get("actions") or []
    node: Dict[str, Any] = {
        "node_type": "action_node",
        "actions": actions,
        "player": first.get("player"),
    }
    if first.get("reach_probs_only"):
        node["reach_probs_only"] = True

    strategy: dict[str, list[float]] = {}
    evs: dict[str, list[float]] = {}
    equities: dict[str, list[float]] = {}
    ip_range: dict[str, float] = {}
    oop_range: dict[str, float] = {}

    for row in rows:
        hand = row.get("hand")
        if not hand:
            continue
        if row.get("strategy_probs") is not None:
            strategy[hand] = row["strategy_probs"]
        if row.get("evs") is not None:
            evs[hand] = row["evs"]
        if row.get("equities") is not None:
            equities[hand] = row["equities"]
        if row.get("ip_range") is not None:
            ip_range[hand] = row["ip_range"]
        if row.get("oop_range") is not None:
            oop_range[hand] = row["oop_range"]

    if strategy:
        node["strategy"] = {"actions": actions, "strategy": strategy}
    if evs:
        node["evs"] = {"actions": actions, "evs": evs}
    if equities:
        node["equities"] = {"actions": actions, "equities": equities}
    if ip_range or oop_range:
        ranges: Dict[str, Any] = {"player": first.get("player")}
        if ip_range:
            ranges["ip_range"] = ip_range
        if oop_range:
            ranges["oop_range"] = oop_range
        node["ranges"] = ranges

    return node
