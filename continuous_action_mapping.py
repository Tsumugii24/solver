#!/usr/bin/env python3
"""
连续动作 → 离散动作映射

真实场景中，人类玩家的 bet/raise 金额是任意且连续的（如 bet 37、raise 25.5）。
策略树只提供离散选项（如 BET 2, BET 25, BET 50）。
本模块将连续输入映射到数值最接近的离散选项。

用法:
  from continuous_action_mapping import map_continuous_to_discrete
  mapped = map_continuous_to_discrete("bet 37", ["CHECK", "BET 2", "BET 25", "BET 50"])
  # -> "BET 25"

  python continuous_action_mapping.py  # 交互测试
"""

import re
from typing import List, Optional, Tuple


# 带金额的动作类型（需做数值映射）
AMOUNT_ACTIONS = ("BET", "RAISE", "DONK")

# 无金额动作类型（按类型匹配）
NO_AMOUNT_ACTIONS = ("CHECK", "CALL", "FOLD", "ALLIN")


def _parse_action(user_input: str) -> Tuple[Optional[str], Optional[float]]:
    """
    解析用户输入，提取动作类型和金额

    Returns:
        (action_type, amount) 如 ("BET", 37.0) 或 ("CHECK", None)
    """
    s = user_input.strip().upper()
    if not s:
        return None, None

    # 提取金额
    nums = re.findall(r"[\d.]+", s)
    amount = float(nums[0]) if nums else None

    # 识别动作类型
    action_type = None
    parts = s.split()
    first_word = parts[0] if parts else ""
    for t in AMOUNT_ACTIONS + NO_AMOUNT_ACTIONS:
        compact_amount = re.match(rf"^{t}[\d.]+$", s)
        if first_word == t or s.startswith(t + " ") or compact_amount:
            action_type = t
            break
    # 若只输入数字（如 "37"），默认视为 BET
    if not action_type and amount is not None:
        action_type = "BET"

    return action_type, amount


def _extract_amount_from_action(action: str) -> Optional[float]:
    """从离散动作中提取金额，如 'BET 25' -> 25.0"""
    nums = re.findall(r"[\d.]+", action)
    return float(nums[0]) if nums else None


def extract_amount_from_action(action: str) -> Optional[float]:
    """从动作字符串提取金额（公开接口）"""
    return _extract_amount_from_action(action)


def extract_actual_amount(user_input: str) -> Optional[float]:
    """
    从用户输入中提取实际金额（用于 bet/raise 的连续值）

    Returns:
        金额，如 "bet 20" -> 20.0；无金额时返回 None
    """
    _, amount = _parse_action(user_input)
    return amount


def _get_actions_of_type(available: List[str], action_type: str) -> List[str]:
    """获取可用动作中指定类型的列表"""
    at_upper = action_type.upper()
    return [a for a in available if a.upper().startswith(at_upper)]


def map_continuous_to_discrete(
    user_input: str, available_actions: List[str]
) -> Optional[str]:
    """
    将任意连续的 bet/raise 输入映射到最接近的离散选项

    规则：
    - 数值更接近哪个离散选项，则映射到该选项
    - CHECK/CALL/FOLD/ALLIN 按类型匹配，无金额
    - BET/RAISE/DONK 按金额距离找最近

    Args:
        user_input: 用户输入，如 "bet 37", "raise 25", "check"
        available_actions: 当前可用的离散动作列表

    Returns:
        映射后的离散动作，如 "BET 25"；无法映射时返回 None
    """
    if not user_input or not available_actions:
        return None

    action_type, user_amount = _parse_action(user_input)
    if not action_type:
        return None

    # 精确匹配
    for a in available_actions:
        if a.upper() == user_input.strip().upper():
            return a

    # 无金额动作：按类型匹配
    if action_type in NO_AMOUNT_ACTIONS:
        candidates = _get_actions_of_type(available_actions, action_type)
        return candidates[0] if candidates else None

    # 带金额动作：找数值最接近的
    if action_type in AMOUNT_ACTIONS:
        candidates = _get_actions_of_type(available_actions, action_type)
        if not candidates:
            return None
        if user_amount is None:
            return candidates[0]  # 未指定金额，取第一个

        best = None
        best_dist = float("inf")
        for a in candidates:
            amt = _extract_amount_from_action(a)
            if amt is not None:
                dist = abs(user_amount - amt)
                if dist < best_dist:
                    best_dist = dist
                    best = a
        return best

    # 未知类型：尝试模糊匹配
    for a in available_actions:
        if a.upper().startswith(action_type):
            return a
    return None


def filter_invalid_raise_actions(
    actions: List[str],
    probs: List[float],
    effective_call_amount: float,
    ev_dict: Optional[dict] = None,
) -> Tuple[List[str], List[float], Optional[dict]]:
    """
    过滤无效的 RAISE/BET/DONK 动作：金额 <= effective_call_amount 的选项不可选
    （如玩家 bet 20 时，RAISE 7 无效，因 call 至少需 20）

    Returns:
        (filtered_actions, renormalized_probs, filtered_ev_dict)
    """
    if effective_call_amount <= 0:
        return actions, probs, ev_dict

    valid_indices = []
    for i, a in enumerate(actions):
        au = a.upper()
        if au.startswith(("RAISE", "BET", "DONK")):
            amt = _extract_amount_from_action(a)
            if amt is not None and amt <= effective_call_amount:
                continue  # 无效：raise 金额必须 > call 金额
        valid_indices.append(i)

    if not valid_indices:
        return actions, probs, ev_dict  # 全部无效时保留原样

    filtered_actions = [actions[i] for i in valid_indices]
    filtered_probs = [probs[i] for i in valid_indices]
    total = sum(filtered_probs)
    if total > 0:
        filtered_probs = [p / total for p in filtered_probs]
    filtered_ev = {a: ev_dict[a] for a in filtered_actions if ev_dict and a in ev_dict} if ev_dict else None
    return filtered_actions, filtered_probs, filtered_ev


def main() -> None:
    """交互测试"""
    print("连续动作 → 离散动作映射 (测试)")
    print("输入 'q' 退出")
    print("-" * 50)

    # 示例可用动作
    sample_actions = [
        "CHECK",
        "BET 2",
        "BET 25",
        "BET 50",
        "BET 100",
        "RAISE 50",
        "RAISE 100",
        "CALL",
        "FOLD",
        "ALLIN",
    ]

    while True:
        try:
            user = input("\n你的输入 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n退出")
            break
        if user.lower() in ("q", "quit", "exit"):
            print("退出")
            break

        result = map_continuous_to_discrete(user, sample_actions)
        if result:
            print(f"  -> 映射到: {result}")
        else:
            print(f"  -> 无法映射，可用: {sample_actions}")


if __name__ == "__main__":
    main()
