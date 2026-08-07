"""Validated cross-process transport for Monitor Setting Library snapshots."""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
from string import Formatter
from typing import Any, Dict, Mapping, MutableMapping, Optional


SETTING_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
MAX_SETTING_ID_LENGTH = 96
MAX_CONFIG_TEMPLATE_BYTES = 256 * 1024
SUPPORTED_CONFIG_PLACEHOLDERS = {
    "pot",
    "effective_stack",
    "board",
    "range_oop",
    "range_ip",
    "estimate_memory_line",
    "thread_num",
    "accuracy",
    "max_iteration",
    "print_interval",
    "use_isomorphism",
    "dump_format",
    "output_file",
}
REQUIRED_CONFIG_PLACEHOLDERS = {
    "pot",
    "effective_stack",
    "board",
    "range_oop",
    "range_ip",
    "estimate_memory_line",
    "thread_num",
    "dump_format",
    "output_file",
}


def encode_solver_setting_snapshot(setting: Mapping[str, Any]) -> str:
    """Encode a Setting snapshot for a command-line argument."""
    normalized = normalize_solver_setting_snapshot(setting)
    payload = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(payload).decode("ascii")


def decode_solver_setting_snapshot(encoded: str) -> Dict[str, Any]:
    """Decode and validate a base64-encoded Setting Library item."""
    if not isinstance(encoded, str) or not encoded.strip():
        raise ValueError("Setting snapshot is empty.")
    try:
        payload = base64.b64decode(encoded.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Setting snapshot is not valid base64.") from exc
    if len(payload) > MAX_CONFIG_TEMPLATE_BYTES * 2:
        raise ValueError("Setting snapshot is too large.")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Setting snapshot is not valid UTF-8 JSON.") from exc
    return normalize_solver_setting_snapshot(value)


def normalize_solver_setting_snapshot(value: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("Setting snapshot must be a JSON object.")
    setting_id = _setting_id(value.get("id"))
    config_template = value.get("configTemplate")
    if not isinstance(config_template, str) or not config_template.strip():
        raise ValueError("Setting configTemplate is required.")
    if len(config_template.encode("utf-8")) > MAX_CONFIG_TEMPLATE_BYTES:
        raise ValueError("Setting configTemplate is too large.")
    _validate_config_template(config_template)
    pot = _positive_number(value.get("pot"), "pot")
    effective_stack = _positive_number(
        value.get("effectiveStack", value.get("effective_stack")),
        "effectiveStack",
    )
    return {
        "id": setting_id,
        "configTemplate": config_template if config_template.endswith("\n") else f"{config_template}\n",
        "pot": pot,
        "effectiveStack": effective_stack,
    }


def register_solver_setting_snapshot(
    encoded: str,
    scenario_config: MutableMapping[str, str],
    scenario_defaults: MutableMapping[str, Dict[str, float]],
    *,
    expected_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Install one snapshot into the current process's scenario registries."""
    setting = decode_solver_setting_snapshot(encoded)
    if expected_id is not None and setting["id"] != expected_id:
        raise ValueError(
            f"Setting snapshot id {setting['id']!r} does not match --scenario {expected_id!r}."
        )
    setting_id = setting["id"]
    scenario_config[setting_id] = setting["configTemplate"]
    scenario_defaults[setting_id] = {
        "pot": setting["pot"],
        "effective_stack": setting["effectiveStack"],
    }
    return setting


def snapshot_for_scenario(
    scenario: str,
    scenario_config: Mapping[str, str],
    scenario_defaults: Mapping[str, Mapping[str, Any]],
) -> str:
    """Serialize the effective in-process scenario for a child solver process."""
    if scenario not in scenario_config or scenario not in scenario_defaults:
        raise ValueError(f"Unknown solver scenario: {scenario}")
    defaults = scenario_defaults[scenario]
    return encode_solver_setting_snapshot(
        {
            "id": scenario,
            "configTemplate": scenario_config[scenario],
            "pot": defaults.get("pot"),
            "effectiveStack": defaults.get("effective_stack"),
        }
    )


def _setting_id(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Setting id is required.")
    text = value.strip()
    if (
        not text
        or len(text) > MAX_SETTING_ID_LENGTH
        or text.startswith((".", "-"))
        or text.endswith((".", "-"))
        or ".." in text
        or "--" in text
        or not SETTING_ID_PATTERN.fullmatch(text)
    ):
        raise ValueError("Setting id must use letters, numbers, '.', '_' or '-'.")
    return text


def _positive_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"Setting {field} must be a positive number.")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Setting {field} must be a positive number.") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"Setting {field} must be a positive number.")
    return int(parsed) if parsed.is_integer() else parsed


def _validate_config_template(template: str) -> None:
    try:
        placeholders = {
            field_name
            for _literal, field_name, _format_spec, _conversion in Formatter().parse(template)
            if field_name
        }
    except ValueError as exc:
        raise ValueError("Setting configTemplate contains invalid braces.") from exc
    unknown = sorted(placeholders - SUPPORTED_CONFIG_PLACEHOLDERS)
    missing = sorted(REQUIRED_CONFIG_PLACEHOLDERS - placeholders)
    if unknown:
        raise ValueError(f"Setting configTemplate has unsupported placeholders: {', '.join(unknown)}.")
    if missing:
        raise ValueError(f"Setting configTemplate is missing placeholders: {', '.join(missing)}.")
