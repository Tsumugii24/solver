#!/usr/bin/env python3
"""
交互式策略查询：Player (OOP) vs AI (IP)

- Player = OOP，先手行动，手动输入策略选择
- AI = IP，后手行动，按策略概率自动选择
- 每次行动前 mock 随机手牌，从 parquet 查询策略信息

用法:
  python interactive_strategy.py cache/Ac2c2d.parquet
"""

import argparse
import random
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

current_dir = Path(__file__).parent
sys.path.insert(0, str(current_dir))

from query_action_line import (
    ActionLineQuery,
    _load_data,
    _auto_detect_config,
)
try:
    from parse_solver_result import parse_config, _expand_range_to_hands
except ImportError:
    parse_config = lambda p: {'board': '', 'ip_range': {}, 'oop_range': {}}
    _expand_range_to_hands = lambda r, b=None: r

try:
    from run_solver import run_solver, SCRIPT_DIR as SOLVER_SCRIPT_DIR
except ImportError:
    run_solver = None
    SOLVER_SCRIPT_DIR = current_dir
from continuous_action_mapping import (
    map_continuous_to_discrete,
    extract_actual_amount,
    extract_amount_from_action,
    filter_invalid_raise_actions,
)
from convert_range_format import dict_to_range_string


def _match_action(user_input: str, available: List[str]) -> Optional[str]:
    """将用户输入匹配到可用动作（支持连续 bet/raise 映射到最近离散选项）"""
    # 优先使用连续→离散映射（真实场景：任意金额映射到最接近的选项）
    mapped = map_continuous_to_discrete(user_input, available)
    if mapped:
        return mapped
    return None


def _pick_hand_from_strategy(strategy_dict: Dict, range_dict: Dict) -> Optional[str]:
    """从策略中随机选一手牌（优先在 range 内的）"""
    hands_in_range = [h for h in strategy_dict if h in range_dict or not range_dict]
    pool = hands_in_range if hands_in_range else list(strategy_dict.keys())
    if not pool:
        return None
    return random.choice(pool)


def _get_probs_for_hand(hand: str, strategy_dict: Dict, actions: List[str]) -> List[float]:
    """获取某手牌的策略概率"""
    # 支持正反序手牌
    for h in (hand, hand[2:4] + hand[0:2] if len(hand) == 4 else hand):
        if h in strategy_dict:
            data = strategy_dict[h]
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get('probs', data.get('prob', data.get('strategy', [])))
    return []


def _get_ev_for_hand(hand: str, evs_dict: Dict, actions: List[str]) -> Optional[Dict[str, float]]:
    """获取某手牌各动作的 EV，返回 {action: ev_value}"""
    if not evs_dict:
        return None
    for h in (hand, hand[2:4] + hand[0:2] if len(hand) == 4 else hand):
        if h in evs_dict:
            ev_values = evs_dict[h]
            if isinstance(ev_values, list) and len(ev_values) == len(actions):
                return dict(zip(actions, ev_values))
            if isinstance(ev_values, (int, float)):
                return {a: ev_values for a in actions}
    return None


def _sample_action_by_probs(actions: List[str], probs: List[float]) -> str:
    """按概率随机选择动作"""
    if not actions or not probs or len(actions) != len(probs):
        return random.choice(actions) if actions else ""
    # 归一化
    total = sum(probs)
    if total <= 0:
        return random.choice(actions)
    probs_norm = [p / total for p in probs]
    return random.choices(actions, weights=probs_norm, k=1)[0]


def _get_street(path_actions: List[str], board_count: int) -> str:
    """根据当前配置的公共牌数推断所在街"""
    if board_count <= 3:
        return 'flop'
    if board_count == 4:
        return 'turn'
    return 'river'


def _get_path_and_initial_for_pot(
    path_display: List[str], board_count: int, initial_pot: float, effective_stack: float
) -> Tuple[List[str], float]:
    """
    获取用于计算当前 pot 的 path 和 initial_pot。
    切换至 turn/river 后，querier.initial_pot 已是该街起始 pot，
    需只传入该街的 actions，避免重复累计前街下注。
    """
    deal_indices = [i for i, a in enumerate(path_display) if a.upper().startswith('DEAL')]
    if board_count == 3:
        return path_display, initial_pot
    if board_count == 4 and deal_indices:
        start = deal_indices[0] + 1
        end = deal_indices[1] if len(deal_indices) > 1 else len(path_display)
        return path_display[start:end], initial_pot
    if board_count == 5 and len(deal_indices) >= 2:
        start = deal_indices[1] + 1
        return path_display[start:], initial_pot
    return path_display, initial_pot


def _calc_street_state(path_actions: List[str], initial_pot: float) -> Tuple[float, float, float]:
    """
    仅按“当前街新增动作”计算底池和双方本街新增投入。

    注意：
    - `initial_pot` 是当前街开始时的 pot
    - 返回的 `oop_added/ip_added` 仅表示本街额外投入，不含前街 commit
    """
    current_pot = initial_pot
    oop_added = 0.0
    ip_added = 0.0
    current_player = 'oop'

    for action in path_actions:
        action_upper = action.upper().strip()

        if action_upper.startswith('DEAL'):
            current_player = 'oop'
            continue

        if action_upper == 'CHECK':
            current_player = 'ip' if current_player == 'oop' else 'oop'
        elif action_upper == 'CALL':
            if current_player == 'oop':
                call_amount = ip_added - oop_added
                oop_added = ip_added
            else:
                call_amount = oop_added - ip_added
                ip_added = oop_added
            current_pot += call_amount
            current_player = 'ip' if current_player == 'oop' else 'oop'
        elif action_upper.startswith(('BET', 'RAISE', 'DONK')):
            parts = action.split()
            amount = float(parts[1]) if len(parts) > 1 else 0.0
            current_pot += amount
            if current_player == 'oop':
                oop_added += amount
            else:
                ip_added += amount
            current_player = 'ip' if current_player == 'oop' else 'oop'
        elif action_upper == 'FOLD':
            break

    return current_pot, oop_added, ip_added


def _export_turn_config_at_flop_end(
    querier: ActionLineQuery,
    path_flop: List[str],
    turn_node: Dict,
    turn_card: str,
    oop_range: Dict[str, float],
    ip_range: Dict[str, float],
    output_dir: Optional[Path] = None,
) -> Optional[Tuple[Path, str]]:
    """
    FLOP 阶段结束时，根据 turn 第一个节点的 oop_range/ip_range 和累计 pot/effective_stack，
    生成新的 turn 配置文件。

    Args:
        querier: 查询器
        path_flop: FLOP 结束时的路径（不含 DEAL）
        turn_node: turn 第一个节点
        turn_card: 发出的 turn 牌
        oop_range: OOP 范围 {combo: prob}
        ip_range: IP 范围 {combo: prob}
        output_dir: 输出目录，默认 solver/cache/configs/

    Returns:
        (config_path, dump_json_name) 或 None
    """
    # 仅在非终止节点导出
    if turn_node.get('node_type') == 'terminal':
        return None

    pot, oop_added, ip_added = _calc_street_state(path_flop, querier.initial_pot)
    # effective_stack 是当前街起始 behind，只扣除本街新增投入
    remaining_oop = querier.effective_stack - oop_added
    remaining_ip = querier.effective_stack - ip_added
    eff_stack = min(remaining_oop, remaining_ip)

    board_base = (querier.board or "").strip()
    board_list = [c.strip() for c in board_base.split(",") if c.strip()]
    new_board = ",".join(board_list + [turn_card]) if board_list else turn_card

    oop_str = dict_to_range_string(oop_range) if oop_range else ""
    ip_str = dict_to_range_string(ip_range) if ip_range else ""

    template_path = current_dir / "docs" / "turn_config.txt"
    if not template_path.exists():
        print(f"[警告] turn_config 模板不存在: {template_path}")
        return None

    with open(template_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # 生成唯一输出文件名（全用_连接，无空格）
    path_suffix = "_".join(path_flop[:3]) if len(path_flop) >= 3 else "_".join(path_flop)
    path_suffix = "".join(c for c in path_suffix if c.isalnum() or c in " _")[:40].replace(" ", "_")
    out_name = f"turn_config_{path_suffix}_{turn_card}.txt"
    out_dir = output_dir or (current_dir / "cache" / "configs")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / out_name

    new_lines = [
        f"set_pot {int(round(pot))}\n",
        f"set_effective_stack {int(round(eff_stack))}\n",
        f"set_board {new_board}\n",
        f"set_range_oop {oop_str}\n",
        f"set_range_ip {ip_str}\n",
    ]
    # 保留第 6 行到倒数第二行
    if len(lines) > 5:
        new_lines.extend(lines[5:-1])
    dump_name = out_name.replace(".txt", ".json")
    new_lines.append(f"dump_result {dump_name}\n")

    with open(out_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    print(f"[FLOP→TURN] 已生成 turn 配置: {out_path}")
    print(f"  Pot={pot:.0f}, EffectiveStack={eff_stack:.0f}, Board={new_board}")
    return (out_path, dump_name)


def _export_river_config_at_turn_end(
    querier: ActionLineQuery,
    path_turn: List[str],
    river_node: Dict,
    river_card: str,
    oop_range: Dict[str, float],
    ip_range: Dict[str, float],
    output_dir: Optional[Path] = None,
) -> Optional[Tuple[Path, str]]:
    """
    TURN 阶段结束时，根据 river 第一个节点的 oop_range/ip_range 和累计 pot/effective_stack，
    生成新的 river 配置文件。

    Args:
        querier: 查询器
        path_turn: TURN 结束时的路径（含 flop+DEAL:turn+turn actions，不含 DEAL:river）
        river_node: river 第一个节点
        river_card: 发出的 river 牌
        oop_range: OOP 范围 {combo: prob}
        ip_range: IP 范围 {combo: prob}
        output_dir: 输出目录，默认 solver/cache/configs/

    Returns:
        (config_path, dump_json_name) 或 None
    """
    if river_node.get('node_type') == 'terminal':
        return None

    path_turn_local, _ = _get_path_and_initial_for_pot(
        path_turn, 4, querier.initial_pot, querier.effective_stack
    )
    pot, oop_added, ip_added = _calc_street_state(path_turn_local, querier.initial_pot)
    remaining_oop = querier.effective_stack - oop_added
    remaining_ip = querier.effective_stack - ip_added
    eff_stack = min(remaining_oop, remaining_ip)

    board_base = (querier.board or "").strip()
    board_list = [c.strip() for c in board_base.split(",") if c.strip()]
    new_board = ",".join(board_list + [river_card]) if board_list else river_card

    oop_str = dict_to_range_string(oop_range) if oop_range else ""
    ip_str = dict_to_range_string(ip_range) if ip_range else ""

    template_path = current_dir / "docs" / "river_config.txt"
    if not template_path.exists():
        print(f"[警告] river_config 模板不存在: {template_path}")
        return None

    with open(template_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    path_suffix = "_".join(path_turn[:5]) if len(path_turn) >= 5 else "_".join(path_turn)
    path_suffix = "".join(c for c in path_suffix if c.isalnum() or c in " _")[:50].replace(" ", "_")
    out_name = f"river_config_{path_suffix}_{river_card}.txt"
    out_dir = output_dir or (current_dir / "cache" / "configs")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / out_name

    new_lines = [
        f"set_pot {int(round(pot))}\n",
        f"set_effective_stack {int(round(eff_stack))}\n",
        f"set_board {new_board}\n",
        f"set_range_oop {oop_str}\n",
        f"set_range_ip {ip_str}\n",
    ]
    if len(lines) > 5:
        new_lines.extend(lines[5:-1])
    dump_name = out_name.replace(".txt", ".json")
    new_lines.append(f"dump_result {dump_name}\n")

    with open(out_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    print(f"[TURN→RIVER] 已生成 river 配置: {out_path}")
    print(f"  Pot={pot:.0f}, EffectiveStack={eff_stack:.0f}, Board={new_board}")
    return (out_path, dump_name)


def _infer_flop_board_from_path(data_path: Path) -> str:
    """
    从 parquet/json 文件名推断 flop 三张公共牌。
    如 Ac2c2d -> Ac,2c,2d；AcTc6c -> Ac,Tc,6c
    """
    stem = data_path.stem
    ranks, suits = set("23456789TJQKAtjqka"), set("cdhs")
    cards = []
    i = 0
    while i < len(stem) - 1 and stem[i] in ranks and stem[i + 1] in suits:
        cards.append(stem[i : i + 2])
        i += 2
    if len(cards) >= 3:
        return ",".join(cards[:3])
    return ""


def _print_leaf_terminal(path_display: List[str], reason: str) -> None:
    """当树中没有显式 terminal 子节点时，按叶子节点展示结束信息。"""
    print("\n" + "=" * 60)
    print("对局结束 (Leaf)")
    print(f"Path: ROOT -> {' -> '.join(path_display) if path_display else '(root)'}")
    print(f"Reason: {reason}")
    print("=" * 60)


def run_interactive(data_file: str) -> None:
    """运行交互式对局"""
    config_path = _auto_detect_config(data_file)
    if config_path:
        print(f"Config: {config_path}")

    querier = ActionLineQuery(data_file, config_path)
    querier.load()

    # 从 parquet 文件名推断 flop 牌面，覆盖通用 config 的 board
    data_path = Path(data_file)
    if data_path.suffix.lower() == ".parquet":
        inferred = _infer_flop_board_from_path(data_path)
        if inferred:
            querier.board = inferred

    board_count = querier._get_board_card_count()
    path_tree: List[str] = []  # 用于树导航（离散）
    path_display: List[str] = []  # 用于显示（含用户实际金额）
    effective_call_amount: float = 0.0  # 当前需跟注的金额，用于过滤无效 raise
    current_node = querier.data
    current_ip_range = querier.initial_ranges['ip'].copy()
    current_oop_range = querier.initial_ranges['oop'].copy()

    print("\n" + "=" * 60)
    print("交互式策略查询 | Player (IP) 先手 vs AI (OOP)")
    print("=" * 60)
    print(f"Board: {querier.board or '(从文件名推断)'}")
    print(f"Pot: {querier.initial_pot}, Stack: {querier.effective_stack}")
    print("输入 'q' 退出 | 支持任意 bet/raise 金额，将自动映射到最接近的离散选项")
    print("=" * 60)

    while True:
        node_type = current_node.get('node_type', '')

        # 更新 range
        ranges_info = current_node.get('ranges', {})
        if isinstance(ranges_info, dict):
            if ranges_info.get('ip_range'):
                current_ip_range = ranges_info['ip_range']
            if ranges_info.get('oop_range'):
                current_oop_range = ranges_info['oop_range']

        if node_type == 'action_node':
            player = current_node.get('player')
            actions = current_node.get('actions', [])
            strategy_info = current_node.get('strategy', {})
            strategy_dict = strategy_info.get('strategy', {})
            evs_info = current_node.get('evs', {})
            evs_dict = evs_info.get('evs', {}) if isinstance(evs_info, dict) else {}

            if not actions or not strategy_dict:
                print("[终端] 无更多决策")
                break

            is_player_turn = player == 1  # Player = IP (树根先手), AI = OOP
            range_dict = current_ip_range if is_player_turn else current_oop_range
            hand = _pick_hand_from_strategy(strategy_dict, range_dict)
            if not hand:
                hand = random.choice(list(strategy_dict.keys()))

            probs = _get_probs_for_hand(hand, strategy_dict, actions)
            ev_dict = _get_ev_for_hand(hand, evs_dict, actions)

            # 保存过滤前的原始数据（用于显示）
            orig_actions = actions.copy()
            orig_probs = probs.copy()
            orig_ev_dict = ev_dict.copy() if ev_dict else None

            # AI 回合：按 effective_call_amount 过滤无效 raise（金额<=call 的选项），并重新归一化概率
            filtered_out = []
            if not is_player_turn and effective_call_amount > 0:
                actions, probs, ev_dict = filter_invalid_raise_actions(
                    actions, probs, effective_call_amount, ev_dict
                )
                filtered_out = [a for a in orig_actions if a not in actions and a.upper().startswith(("RAISE", "BET", "DONK"))]

            path_for_pot, init_pot = _get_path_and_initial_for_pot(
                path_display, board_count, querier.initial_pot, querier.effective_stack
            )
            pot, _, _ = _calc_street_state(path_for_pot, init_pot)
            street = _get_street(path_display, board_count)
            role = "IP (你)" if is_player_turn else "OOP (AI)"

            print(f"\n--- {street.upper()} | {role} | Pot={pot:.0f} ---")
            print(f"Path: ROOT -> {' -> '.join(path_display) if path_display else '(root)'}")
            if path_tree != path_display or filtered_out:
                print(f"Query path: ROOT -> {' -> '.join(path_tree) if path_tree else '(root)'}")
            if filtered_out:
                print(f"（已过滤 {filtered_out}：金额 <= call {effective_call_amount:.0f}）")
            print(f"Hand: {hand}")
            print(f"Actions: {actions}")

            # 展示策略概率
            if filtered_out:
                print("\nStrategy (查询原始):")
                for act, p in zip(orig_actions, orig_probs):
                    bar = '#' * int(p * 20)
                    ev_str = f"  EV={orig_ev_dict[act]:+.3f}" if orig_ev_dict and act in orig_ev_dict else ""
                    mark = " [已过滤]" if act in filtered_out else ""
                    print(f"  {act:<25} {p:>6.1%}  {bar}{ev_str}{mark}")
                print("\nStrategy (过滤后):")
                for act, p in zip(actions, probs):
                    bar = '#' * int(p * 20)
                    ev_str = f"  EV={ev_dict[act]:+.3f}" if ev_dict and act in ev_dict else ""
                    print(f"  {act:<25} {p:>6.1%}  {bar}{ev_str}")
            else:
                print("\nStrategy (概率):")
                for act, p in zip(actions, probs):
                    bar = '#' * int(p * 20)
                    ev_str = f"  EV={ev_dict[act]:+.3f}" if ev_dict and act in ev_dict else ""
                    print(f"  {act:<25} {p:>6.1%}  {bar}{ev_str}")

            if is_player_turn:
                # Player 手动输入
                while True:
                    try:
                        user = input("\n你的选择 > ").strip()
                    except (EOFError, KeyboardInterrupt):
                        print("\n退出")
                        return
                    if user.lower() in ('q', 'quit', 'exit'):
                        print("退出")
                        return
                    tree_action = _match_action(user, actions)
                    if tree_action:
                        # 显示用实际金额，树导航用离散
                        actual_amt = extract_actual_amount(user)
                        if actual_amt is not None and tree_action.upper().startswith(("BET", "RAISE", "DONK")):
                            action_type = tree_action.split()[0]
                            path_display.append(f"{action_type} {actual_amt:.0f}")
                            effective_call_amount = actual_amt
                        else:
                            path_display.append(tree_action)
                            effective_call_amount = 0.0
                        action = tree_action
                        break
                    print(f"无效输入，可选: {actions}")
            else:
                # AI (OOP) 按概率权重随机选择（非选最高）
                action = _sample_action_by_probs(actions, probs)
                chosen_prob = next((p for a, p in zip(actions, probs) if a == action), 0)
                print(f"\nAI 选择: {action} ({chosen_prob:.1%} 概率，按权重随机抽样)")
                path_display.append(action)
                # AI 的 bet/raise 更新 effective_call_amount；CHECK/CALL/FOLD 则清零
                if action.upper().startswith(("BET", "RAISE", "DONK")):
                    amt = extract_amount_from_action(action)
                    effective_call_amount = amt if amt is not None else 0.0
                else:
                    effective_call_amount = 0.0

            path_tree.append(action)
            current_node = current_node.get('childrens', {}).get(action)
            if not current_node:
                if action.upper() == 'FOLD':
                    _print_leaf_terminal(path_display, "fold")
                else:
                    _print_leaf_terminal(path_display, "no further child node")
                break

        elif node_type == 'chance_node':
            dealcards = current_node.get('dealcards', {})
            if not dealcards:
                print("[终端]")
                break
            is_flop_to_turn = board_count == 3 and _get_street(path_display, board_count) == 'flop'
            is_turn_to_river = board_count == 4  # turn 配置下遇到 chance_node 即发 river
            card = random.choice(list(dealcards.keys()))
            path_tree.append(f"DEAL:{card}")
            path_display.append(f"DEAL:{card}")
            effective_call_amount = 0.0  # 新街重置
            print(f"\n--- 发牌: {card} ---")
            next_node = dealcards[card]
            if not next_node:
                _print_leaf_terminal(path_display, "no further child node after deal")
                break
            current_node = next_node

            # FLOP→TURN：导出 turn 配置、跑 turn 解算、切换为 turn JSON
            if is_flop_to_turn and next_node.get('node_type') != 'terminal':
                turn_ranges = next_node.get('ranges', {})
                turn_oop = turn_ranges.get('oop_range') or current_oop_range
                turn_ip = turn_ranges.get('ip_range') or current_ip_range
                path_flop = [a for a in path_display if not a.upper().startswith('DEAL')]
                export_result = _export_turn_config_at_flop_end(
                    querier, path_flop, next_node, card, turn_oop, turn_ip
                )
                if export_result and run_solver:
                    config_path, dump_name = export_result
                    print(f"\n[FLOP→TURN] 正在运行 turn 解算...")
                    output_dir = str(SOLVER_SCRIPT_DIR / "cache" / "results")
                    result = run_solver(str(Path(config_path).resolve()), output_dir=output_dir)
                    if result.get('success'):
                        turn_json_path = SOLVER_SCRIPT_DIR / "cache" / "results" / dump_name
                        if turn_json_path.exists():
                            turn_data = _load_data(turn_json_path)
                            querier.data = turn_data
                            querier.data_path = Path(turn_json_path)
                            querier.config_path = Path(config_path)
                            cfg = parse_config(str(config_path))
                            querier.board = cfg.get('board', '')
                            with open(config_path, 'r', encoding='utf-8') as f:
                                for line in f:
                                    line = line.strip()
                                    if line.startswith('set_pot'):
                                        querier.initial_pot = float(line.split()[1])
                                    elif line.startswith('set_effective_stack'):
                                        querier.effective_stack = float(line.split()[1])
                            board_list = [c.strip() for c in querier.board.split(',') if c.strip()]
                            querier.initial_ranges = {
                                'ip': _expand_range_to_hands(cfg.get('ip_range', {}), board_list),
                                'oop': _expand_range_to_hands(cfg.get('oop_range', {}), board_list),
                            }
                            board_count = 4
                            current_node = turn_data
                            current_ip_range = turn_ranges.get('ip_range') or current_ip_range
                            current_oop_range = turn_ranges.get('oop_range') or current_oop_range
                            print(f"[FLOP→TURN] 已加载 turn 策略: {turn_json_path}")
                        else:
                            print(f"[警告] turn 解算完成但未找到输出: {turn_json_path}")
                    else:
                        print(f"[警告] turn 解算失败: {result.get('error', 'unknown')}，继续使用 flop 树")
                elif export_result and not run_solver:
                    print("[警告] run_solver 不可用，无法运行 turn 解算")

            # TURN→RIVER：导出 river 配置、跑 river 解算、切换为 river JSON
            elif is_turn_to_river and next_node.get('node_type') != 'terminal':
                river_ranges = next_node.get('ranges', {})
                river_oop = river_ranges.get('oop_range') or current_oop_range
                river_ip = river_ranges.get('ip_range') or current_ip_range
                path_turn = path_display[:-1]  # 排除刚加的 DEAL:river
                export_result = _export_river_config_at_turn_end(
                    querier, path_turn, next_node, card, river_oop, river_ip
                )
                if export_result and run_solver:
                    config_path, dump_name = export_result
                    print(f"\n[TURN→RIVER] 正在运行 river 解算...")
                    output_dir = str(SOLVER_SCRIPT_DIR / "cache" / "results")
                    result = run_solver(str(Path(config_path).resolve()), output_dir=output_dir)
                    if result.get('success'):
                        river_json_path = SOLVER_SCRIPT_DIR / "cache" / "results" / dump_name
                        if river_json_path.exists():
                            river_data = _load_data(river_json_path)
                            querier.data = river_data
                            querier.data_path = Path(river_json_path)
                            querier.config_path = Path(config_path)
                            cfg = parse_config(str(config_path))
                            querier.board = cfg.get('board', '')
                            with open(config_path, 'r', encoding='utf-8') as f:
                                for line in f:
                                    line = line.strip()
                                    if line.startswith('set_pot'):
                                        querier.initial_pot = float(line.split()[1])
                                    elif line.startswith('set_effective_stack'):
                                        querier.effective_stack = float(line.split()[1])
                            board_list = [c.strip() for c in querier.board.split(',') if c.strip()]
                            querier.initial_ranges = {
                                'ip': _expand_range_to_hands(cfg.get('ip_range', {}), board_list),
                                'oop': _expand_range_to_hands(cfg.get('oop_range', {}), board_list),
                            }
                            board_count = 5
                            current_node = river_data
                            current_ip_range = river_ranges.get('ip_range') or current_ip_range
                            current_oop_range = river_ranges.get('oop_range') or current_oop_range
                            print(f"[TURN→RIVER] 已加载 river 策略: {river_json_path}")
                        else:
                            print(f"[警告] river 解算完成但未找到输出: {river_json_path}")
                    else:
                        print(f"[警告] river 解算失败: {result.get('error', 'unknown')}，继续使用 turn 树")
                elif export_result and not run_solver:
                    print("[警告] run_solver 不可用，无法运行 river 解算")

        elif node_type == 'terminal':
            value = current_node.get('value', 0)
            print("\n" + "=" * 60)
            print("对局结束 (Terminal)")
            print(f"Path: ROOT -> {' -> '.join(path_display)}")
            print(f"Value: {value}")
            print("=" * 60)
            break
        else:
            print(f"[未知节点类型: {node_type}]")
            break


def main() -> None:
    parser = argparse.ArgumentParser(description="交互式策略查询: Player (IP) vs AI (OOP)")
    parser.add_argument("data", nargs="?", default="cache/Ac2c2d.parquet",
                        help="JSON 或 Parquet 策略文件")
    args = parser.parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        if data_path.suffix.lower() == ".parquet":
            print(f"文件不存在，尝试从 HuggingFace 下载: {data_path.stem}")
            try:
                from upload.download_from_hf import download_board_from_hf
                cache_dir = str(data_path.parent) if data_path.parent != Path(".") else "cache"
                downloaded = download_board_from_hf(data_path.stem, cache_dir=cache_dir)
                if downloaded and downloaded.exists():
                    data_path = downloaded
                    print(f"已下载: {data_path}")
                else:
                    print(f"[错误] 文件不存在且下载失败: {data_path}")
                    sys.exit(1)
            except ImportError:
                print(f"[错误] 文件不存在: {data_path}（需安装 huggingface_hub 以支持自动下载）")
                sys.exit(1)
        else:
            print(f"[错误] 文件不存在: {data_path}")
            sys.exit(1)

    run_interactive(str(data_path))


if __name__ == "__main__":
    main()
