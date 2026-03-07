import json
import csv
import random
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

try:
    import pyarrow.parquet as pq
except ImportError:
    pq = None  # type: ignore

# Add the current directory to sys.path to allow importing from parse_solver_result
# Assuming this script is in solver/ and parse_solver_result.py is also in solver/
current_dir = Path(__file__).parent
sys.path.append(str(current_dir))

try:
    from parse_solver_result import parse_config, _expand_range_to_hands
except ImportError:
    # Fallback if import fails (e.g. running from different directory)
    print("Warning: Could not import helper functions from parse_solver_result.py")
    def parse_config(path): return {'board': '', 'ip_range': {}, 'oop_range': {}}
    def _expand_range_to_hands(r, b): return r


def _load_data(path: Path) -> Dict[str, Any]:
    """从 JSON 或 Parquet 文件加载策略树数据"""
    path = Path(path)
    if path.suffix.lower() == '.parquet':
        if pq is None:
            raise RuntimeError("请安装 pyarrow: pip install pyarrow")
        table = pq.read_table(path)
        records = table.to_pylist()
        if not records:
            raise ValueError(f"Parquet 文件为空: {path}")
        raw = records[0].get("data")
        return json.loads(raw) if isinstance(raw, str) else raw
    else:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)


def calc_pot_along_path(path_actions: List[str], initial_pot: float, effective_stack: float) -> Tuple[float, float, float]:
    """
    沿着 action line 计算当前底池大小和双方已投入筹码
    
    Solver 中的下注金额都是绝对值（实际筹码数）。
    - BET X:   当前玩家投入 X，pot += X
    - RAISE X: 当前玩家投入 X，pot += X
    - CALL:    补齐到对手水平，pot += 差额
    - CHECK:   不投入
    - DEAL:XX: 发牌，不影响底池
    
    Returns:
        (current_pot, oop_invested, ip_invested)
    """
    current_pot = initial_pot
    oop_invested = initial_pot / 2
    ip_invested = initial_pot / 2

    # OOP = player 0 先行动
    current_player = 'oop'
    
    for action in path_actions:
        action_upper = action.upper().strip()
        
        if action_upper.startswith('DEAL'):
            # 发牌：新街开始，OOP 先行动
            current_player = 'oop'
            continue
        
        if action_upper == 'CHECK':
            current_player = 'ip' if current_player == 'oop' else 'oop'
            
        elif action_upper == 'CALL':
            # CALL: 补齐到对手水平，增加底池
            if current_player == 'oop':
                call_amount = ip_invested - oop_invested
                oop_invested = ip_invested
            else:
                call_amount = oop_invested - ip_invested
                ip_invested = oop_invested
            current_pot += call_amount
            current_player = 'ip' if current_player == 'oop' else 'oop'
            
        elif action_upper.startswith('BET') or action_upper.startswith('RAISE') or action_upper.startswith('DONK'):
            parts = action.split()
            amount = float(parts[1]) if len(parts) > 1 else 0
            current_pot += amount
            if current_player == 'oop':
                oop_invested += amount
            else:
                ip_invested += amount
            current_player = 'ip' if current_player == 'oop' else 'oop'
            
        elif action_upper == 'FOLD':
            break
    
    return current_pot, oop_invested, ip_invested


class ActionLineQuery:
    def __init__(self, data_path: str, config_path: Optional[str] = None):
        """data_path: JSON 或 Parquet 文件路径"""
        self.data_path = Path(data_path)
        self.config_path = Path(config_path) if config_path else None
        self.data = None
        self.initial_ranges = {'ip': {}, 'oop': {}}
        self.board = ""
        self.initial_pot = 5.0
        self.effective_stack = 100.0

        if self.config_path and self.config_path.exists():
            try:
                config_data = parse_config(str(self.config_path))
                self.board = config_data.get('board', '')
                board_list = [c.strip() for c in self.board.split(',') if c.strip()]
                
                self.initial_ranges = {
                    'ip': _expand_range_to_hands(config_data.get('ip_range', {}), board_list),
                    'oop': _expand_range_to_hands(config_data.get('oop_range', {}), board_list)
                }
                
                # 解析 pot 和 effective_stack
                with open(str(self.config_path), 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith('set_pot'):
                            self.initial_pot = float(line.split()[1])
                        elif line.startswith('set_effective_stack'):
                            self.effective_stack = float(line.split()[1])
                
                print(f"Loaded config: Board={self.board}, Pot={self.initial_pot}, Stack={self.effective_stack}")
            except Exception as e:
                print(f"Error loading config: {e}")

    def load(self):
        fmt = "Parquet" if self.data_path.suffix.lower() == '.parquet' else "JSON"
        print(f"Loading {fmt}: {self.data_path}")
        self.data = _load_data(self.data_path)
        print(f"{fmt} loaded.")

    def _parse_action_line(self, action_line_str: str) -> List[str]:
        """解析动作路径字符串为列表"""
        if "->" in action_line_str:
            actions = [a.strip() for a in action_line_str.split("->")]
        else:
            actions = [a.strip() for a in action_line_str.split(",")]
        
        # 去掉开头的 ROOT 和空元素
        actions = [a for a in actions if a and a.upper() != "ROOT"]
        return actions

    def _navigate_to_node(self, actions: List[str]) -> Optional[Tuple[Dict, List[str], Dict, Dict]]:
        """
        沿着动作路径导航到目标节点，直接使用 JSON 中的绝对值动作
        
        Returns:
            (node, path, ip_range, oop_range) 或 None（路径无效时）
        """
        current_node = self.data
        current_path = []
        current_ip_range = self.initial_ranges['ip'].copy()
        current_oop_range = self.initial_ranges['oop'].copy()

        for action in actions:
            node_type = current_node.get('node_type', '')
            
            # 更新 range
            ranges_info = current_node.get('ranges', {})
            if isinstance(ranges_info, dict):
                if ranges_info.get('ip_range'):
                    current_ip_range = ranges_info['ip_range']
                if ranges_info.get('oop_range'):
                    current_oop_range = ranges_info['oop_range']

            if node_type == 'action_node':
                children = current_node.get('childrens', {})
                
                if action not in children:
                    print(f"[X] Action '{action}' not found at path {' -> '.join(current_path) if current_path else 'ROOT'}")
                    print(f"   Available actions: {sorted(children.keys())}")
                    return None
                
                current_node = children[action]
                current_path.append(action)

            elif node_type == 'chance_node':
                card = None
                if action.startswith("DEAL:"):
                    card = action.split(":")[1].strip()
                elif action.startswith("DEAL "):
                    card = action.split(None, 1)[1].strip()
                else:
                    dealcards = current_node.get('dealcards', {})
                    if action in dealcards:
                        card = action

                if card:
                    dealcards = current_node.get('dealcards', {})
                    if card in dealcards:
                        current_node = dealcards[card]
                        current_path.append(f"DEAL:{card}")
                    else:
                        print(f"[X] Card '{card}' not found at chance node.")
                        print(f"   Available cards: {list(dealcards.keys())[:20]}{'...' if len(dealcards) > 20 else ''}")
                        return None
                else:
                    print(f"[X] Invalid chance action '{action}'. Expected 'DEAL: <card>'")
                    return None
            else:
                print(f"[X] Hit terminal or unknown node type '{node_type}' before finishing path.")
                return None

        return (current_node, current_path, current_ip_range, current_oop_range)

    def _extract_node_data(self, node: Dict, path: List[str], ip_range: Dict, oop_range: Dict) -> List[Dict[str, Any]]:
        """
        从目标节点提取所有手牌的 strategy/ev/equity/range 数据
        
        Returns:
            数据行列表，每行一个手牌
        """
        node_type = node.get('node_type', 'unknown')
        if node_type != 'action_node':
            return []

        player = node.get('player')
        actions = node.get('actions', [])
        strategy_info = node.get('strategy', {})
        strategy_dict = strategy_info.get('strategy', {})

        # EV
        evs_info = node.get('evs', {})
        evs_dict = evs_info.get('evs', {}) if isinstance(evs_info, dict) else {}

        # Equity
        equities_info = node.get('equities', {})
        equities_dict = equities_info.get('equities', {}) if isinstance(equities_info, dict) else {}

        # Range
        ranges_info = node.get('ranges', {})
        final_ip_range = (ranges_info.get('ip_range') if isinstance(ranges_info, dict) else None) or ip_range
        final_oop_range = (ranges_info.get('oop_range') if isinstance(ranges_info, dict) else None) or oop_range

        path_str = ' -> '.join(path) if path else 'ROOT'
        rows = []

        for hand, hand_data in strategy_dict.items():
            # 策略概率
            if isinstance(hand_data, dict):
                probs = hand_data.get('probs', hand_data.get('prob', hand_data.get('strategy', [])))
            elif isinstance(hand_data, list):
                probs = hand_data
            else:
                probs = [hand_data] if isinstance(hand_data, (int, float)) else []

            # EV
            ev = None
            if evs_dict and hand in evs_dict:
                ev_values = evs_dict[hand]
                if isinstance(ev_values, list) and len(ev_values) == len(actions):
                    ev = dict(zip(actions, ev_values))
                elif isinstance(ev_values, (int, float)):
                    ev = ev_values

            # Equity
            equity = None
            if equities_dict and hand in equities_dict:
                equity_values = equities_dict[hand]
                if isinstance(equity_values, list) and len(equity_values) == len(actions):
                    equity = dict(zip(actions, equity_values))
                elif isinstance(equity_values, (int, float)):
                    equity = equity_values

            rows.append({
                'path': path_str,
                'player': player,
                'hand': hand,
                'actions': actions,
                'probs': probs,
                'action_probs': dict(zip(actions, probs)) if probs else {},
                'ip_range': final_ip_range,
                'oop_range': final_oop_range,
                'ev': ev,
                'equity': equity
            })

        return rows

    def query(self, action_line_str: str):
        """查询并打印指定路径的节点信息"""
        if self.data is None:
            self.load()

        actions = self._parse_action_line(action_line_str)
        print(f"Querying path: ROOT -> {' -> '.join(actions)}")

        result = self._navigate_to_node(actions)
        if result is None:
            return

        node, path, ip_range, oop_range = result
        self._print_node_info(node, path, ip_range, oop_range)

    def _get_board_card_count(self) -> int:
        """获取初始 board 的牌数"""
        if self.board:
            return len([c.strip() for c in self.board.split(',') if c.strip()])
        # 从文件名推断：AcTc6c = 3张(flop), AcTc6c6h = 4张(turn)
        stem = self.data_path.stem
        count = 0
        i = 0
        ranks = set('23456789TJQKAtjqka')
        suits = set('cdhs')
        while i < len(stem) - 1:
            if stem[i] in ranks and stem[i+1] in suits:
                count += 1
                i += 2
            else:
                break
        return count if count >= 3 else 3  # 至少 flop

    def _get_current_street(self, path_actions: List[str]) -> str:
        """根据 board 和 path 中 DEAL 的数量推断当前街"""
        board_count = self._get_board_card_count()
        deal_count = sum(1 for a in path_actions if a.upper().startswith('DEAL'))
        
        total_cards = board_count + deal_count
        if total_cards <= 3:
            return 'flop'
        elif total_cards == 4:
            return 'turn'
        else:
            return 'river'

    def query_hand(self, action_line_str: str, hand: str):
        """查询指定路径下某一手牌的完整策略信息"""
        if self.data is None:
            self.load()

        actions = self._parse_action_line(action_line_str)
        path_display = ' -> '.join(actions) if actions else 'ROOT'
        print(f"Querying: [{hand}] at ROOT -> {path_display}")

        result = self._navigate_to_node(actions)
        if result is None:
            return

        node, path, ip_range, oop_range = result
        rows = self._extract_node_data(node, path, ip_range, oop_range)
        if not rows:
            print("该节点没有策略数据（可能不是 action_node）。")
            return

        # 查找目标手牌（支持正反顺序，如 4h5h 和 5h4h）
        hand = hand.strip()
        reverse_hand = hand[2:4] + hand[0:2] if len(hand) == 4 else None
        target = None
        for row in rows:
            if row['hand'] == hand or (reverse_hand and row['hand'] == reverse_hand):
                target = row
                break

        if target is None:
            print(f"未找到手牌 [{hand}]。")
            available = [r['hand'] for r in rows]
            similar = [h for h in available if hand[0:2] in h or hand[2:4] in h] if len(hand) == 4 else []
            if similar:
                print(f"  相近手牌: {similar[:10]}")
            return

        # 计算底池
        pot, oop_inv, ip_inv = calc_pot_along_path(path, self.initial_pot, self.effective_stack)
        
        # 路径显示
        path_display = ' -> '.join(path)
        
        player = target['player']
        player_str = "IP" if player == 1 else "OOP" if player == 0 else str(player)
        action_list = target['actions']
        probs = target['probs']
        street = self._get_current_street(path)

        print("\n" + "=" * 60)
        print(f"Path:    ROOT {self.initial_pot:.0f} -> {path_display}")
        print(f"Street:  {street.upper()}")
        print(f"Player:  {player_str}")
        print(f"Pot:     {pot:.0f}")
        print(f"Hand:    {target['hand']}")
        print("=" * 60)

        # Strategy
        print("\n--- Strategy ---")
        for act, prob in zip(action_list, probs):
            bar = '#' * int(prob * 30)
            print(f"  {act:<30s} {prob:>7.2%}  {bar}")

        # EV
        if target['ev']:
            print("\n--- EV ---")
            ev = target['ev']
            if isinstance(ev, dict):
                for act, val in ev.items():
                    print(f"  {act:<16s} {val:>+.3f}")
            else:
                print(f"  EV: {ev:+.3f}")

        # Equity
        if target['equity']:
            print("\n--- Equity ---")
            eq = target['equity']
            if isinstance(eq, dict):
                for act, val in eq.items():
                    print(f"  {act:<16s} {val:.4f}")
            else:
                print(f"  Equity: {eq:.4f}")

        # Full Range
        ip_r = target['ip_range']
        oop_r = target['oop_range']

        if oop_r:
            print(f"\n--- OOP Range ({len(oop_r)} hands) ---")
            print(self._format_range(oop_r))

        if ip_r:
            print(f"\n--- IP Range ({len(ip_r)} hands) ---")
            print(self._format_range(ip_r))

        print()

    @staticmethod
    def _format_range(range_dict: Dict[str, float]) -> str:
        """将 range 字典格式化为逗号分隔字符串"""
        parts = []
        for hand, prob in range_dict.items():
            if prob >= 1.0 - 1e-6:
                parts.append(hand)
            else:
                parts.append(f"{hand}:{prob:.3f}")
        return ','.join(parts)

    def query_and_export(self, action_line_str: str, output_csv: str):
        """查询指定路径的节点并导出为 CSV"""
        if self.data is None:
            self.load()

        actions = self._parse_action_line(action_line_str)
        print(f"Querying path: ROOT -> {' -> '.join(actions)}")

        result = self._navigate_to_node(actions)
        if result is None:
            return

        node, path, ip_range, oop_range = result

        # 先打印摘要
        self._print_node_info(node, path, ip_range, oop_range)

        # 提取完整数据并导出 CSV
        rows = self._extract_node_data(node, path, ip_range, oop_range)
        if not rows:
            print("该节点没有策略数据可导出（可能不是 action_node）。")
            return

        self._export_csv(rows, output_csv)

    def _export_csv(self, rows: List[Dict[str, Any]], output_csv: str):
        """将数据行列表导出为 CSV 文件"""
        fieldnames = ['path', 'player', 'hand', 'actions', 'probs', 'board',
                       'ip_range', 'oop_range', 'ev', 'equity']

        def _to_str(val):
            if val is None:
                return ''
            if isinstance(val, (dict, list)):
                return json.dumps(val, ensure_ascii=False)
            return str(val)

        count = 0
        with open(output_csv, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for row in rows:
                writer.writerow({
                    'path': row['path'],
                    'player': row['player'],
                    'hand': row['hand'],
                    'actions': '|'.join(row['actions']),
                    'probs': '|'.join(f"{p:.4f}" for p in row['probs']),
                    'board': self.board,
                    'ip_range': _to_str(row['ip_range']),
                    'oop_range': _to_str(row['oop_range']),
                    'ev': _to_str(row['ev']),
                    'equity': _to_str(row['equity']),
                })
                count += 1

        print(f"\n[OK] CSV 导出完成: {output_csv}")
        print(f"  共 {count} 条策略数据（手牌）")

    def _print_node_info(self, node, path, ip_range, oop_range):
        """打印节点摘要信息"""
        node_type = node.get('node_type', 'unknown')
        print("\n" + "="*60)
        print(f"[OK] Found Node at: ROOT -> {' -> '.join(path)}")
        print(f"Type: {node_type}")
        print("="*60)

        if node_type == 'action_node':
            player = node.get('player')
            player_str = "IP" if player == 1 else "OOP" if player == 0 else str(player)
            print(f"Player: {player_str}")
            
            strategy_info = node.get('strategy', {})
            strategy_dict = strategy_info.get('strategy', {})
            actions = node.get('actions', [])
            
            print(f"Available Actions: {actions}")
            print(f"Strategy Entries: {len(strategy_dict)}")
            
            ranges_info = node.get('ranges', {})
            final_ip_range = (ranges_info.get('ip_range') if isinstance(ranges_info, dict) else None) or ip_range
            final_oop_range = (ranges_info.get('oop_range') if isinstance(ranges_info, dict) else None) or oop_range
            
            print(f"IP Range Size: {len(final_ip_range)}")
            print(f"OOP Range Size: {len(final_oop_range)}")

            print("-" * 30)
            print("Strategy (Top 20 hands):")
            
            sorted_hands = []
            for hand, data in strategy_dict.items():
                if isinstance(data, dict):
                    probs = data.get('probs', data.get('prob', []))
                elif isinstance(data, list):
                    probs = data
                else:
                    probs = []
                if probs:
                    sorted_hands.append((hand, probs))
            
            sorted_hands.sort(key=lambda x: max(x[1]) if x[1] else 0, reverse=True)
            
            for hand, probs in sorted_hands[:20]:
                probs_str = ", ".join([f"{p:.4f}" for p in probs])
                print(f"  {hand}: [{probs_str}]")
            
            if len(sorted_hands) > 20:
                print(f"  ... and {len(sorted_hands) - 20} more hands")

        elif node_type == 'chance_node':
            print("Chance Node")
            dealcards = node.get('dealcards', {})
            print(f"Possible Deals: {len(dealcards)}")
            print(f"Cards: {list(dealcards.keys())[:15]}{'...' if len(dealcards) > 15 else ''}")

        elif node_type == 'terminal':
            print("Terminal Node")
            print(f"Value: {node.get('value')}")


def generate_random_action_line(data_file: str) -> str:
    """
    随机生成一条从 ROOT 到终端节点前最后一个 action_node 的 action line

    随机走完整棵树直到 terminal，然后返回最后一个 action_node 的路径，
    确保该路径一定能查到策略数据。

    Args:
        data_file: JSON 或 Parquet 文件路径

    Returns:
        action line 字符串，如 "ROOT, BET 2, RAISE 7, CALL, DEAL: 6h, BET 10"
    """
    data = _load_data(Path(data_file))
    
    path = []
    current_node = data
    last_action_node_path = []
    
    while True:
        node_type = current_node.get('node_type', '')
        
        if node_type == 'action_node':
            last_action_node_path = path.copy()
            children = current_node.get('childrens', {})
            if not children:
                break
            # 随机选择一个动作
            action = random.choice(list(children.keys()))
            path.append(action)
            current_node = children[action]
            
        elif node_type == 'chance_node':
            dealcards = current_node.get('dealcards', {})
            if not dealcards:
                break
            # 随机选择一张牌
            card = random.choice(list(dealcards.keys()))
            path.append(f"DEAL: {card}")
            current_node = dealcards[card]
            
        elif node_type == 'terminal':
            # 到达终端节点
            break
        else:
            # 未知节点类型
            break
    
    # 始终返回最后一个 action_node 的路径（终端节点前的决策点）
    final_path = last_action_node_path if last_action_node_path else path
    
    return "ROOT, " + ", ".join(final_path) if final_path else "ROOT"


def _auto_detect_config(data_file: str) -> Optional[str]:
    """自动查找配置文件（支持 JSON 或 Parquet 文件名）"""
    stem = Path(data_file).stem
    data_path = Path(data_file)
    candidates = [
        Path('solver/configs') / (stem + '.txt'),
        Path('configs') / (stem + '.txt'),
        data_path.parent / (stem + '.txt'),
        # docs 目录下的通用配置
        Path('docs/flop_config.txt'),
        Path('docs/turn_config.txt'),
        Path('docs/river_config.txt'),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def _make_csv_filename(data_file: str, action_line: str) -> str:
    """根据文件名和 action line 自动生成 CSV 文件名"""
    stem = Path(data_file).stem
    # "ROOT, CHECK, CHECK, DEAL: 6h" -> "CHECK_CHECK_DEAL-6h"
    parts = []
    for a in action_line.replace("ROOT", "").split(","):
        a = a.strip()
        if not a:
            continue
        a = a.replace("DEAL:", "DEAL-").replace(" ", "")
        parts.append(a)
    suffix = "_".join(parts) if parts else "ROOT"
    return f"{stem}_{suffix}.csv"


def main(data_file: str, action_line: str = None, hand: str = None, output_csv: str = None,
         random_line: bool = False):
    """
    主函数

    Args:
        data_file: JSON 或 Parquet 文件路径
        action_line: action line 字符串，使用绝对值（如 "ROOT, BET 2, RAISE 7, CALL"）
        hand: 指定手牌（如 '4h5h'），'random' 则随机选，None 则导出整个节点
        output_csv: 输出 CSV 文件名
        random_line: 是否随机生成 action line
    """
    config_path = _auto_detect_config(data_file)
    if config_path:
        print(f"Auto-detected config: {config_path}")

    # 如果需要随机生成 action line
    if random_line or action_line is None:
        action_line = generate_random_action_line(data_file)
        print(f"Generated random action line: {action_line}")

    querier = ActionLineQuery(data_file, config_path)

    # 如果 hand='random'，先导航到节点再随机选一个手牌
    if hand == 'random':
        if querier.data is None:
            querier.load()
        actions = querier._parse_action_line(action_line)
        result = querier._navigate_to_node(actions)
        if result:
            node = result[0]
            strategy_info = node.get('strategy', {})
            strategy_dict = strategy_info.get('strategy', {})
            if strategy_dict:
                hand = random.choice(list(strategy_dict.keys()))
                print(f"Random hand: {hand}")
            else:
                print("该节点没有策略数据，无法随机选手牌。")
                return

    if hand:
        # 查询特定手牌
        querier.query_hand(action_line, hand)
    elif output_csv is not None:
        # 导出 CSV
        querier.query_and_export(action_line, output_csv)
    else:
        # 默认：导出 CSV
        output_csv = _make_csv_filename(data_file, action_line)
        querier.query_and_export(action_line, output_csv)


if __name__ == "__main__":
    # 支持 JSON 或 Parquet
    data_file = 'cache/Ac2c2d.parquet'  # 或 'results/AcTc6c.json'

    # 示例 1: 手动指定 action line（使用绝对值）
    # action_line = 'ROOT, BET 2, RAISE 7, RAISE 15, CALL, DEAL: 6h, BET 20'
    # hand = '4h5h'
    # main(data_file, action_line, hand=hand)

    # 示例 2: 随机生成 action line，导出 CSV
    # main(data_file, random_line=True)

    # 当前运行：随机生成 action line + 随机手牌
    main(data_file, random_line=True, hand='random')
