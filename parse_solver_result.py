"""
德州扑克CFR策略树解析器
将树结构解析成扁平化的数据线（每条数据线代表一条完整的决策路径）
"""

import json
import csv
from typing import Generator, Dict, Any, List, Optional
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class ActionLine:
    """
    代表一条完整的决策路径数据线
    """
    # 路径信息
    path: str                           # 完整的动作路径，如 "CHECK -> BET 25 -> CALL"
    path_actions: List[str]             # 动作列表
    depth: int                          # 路径深度
    
    # 当前节点信息
    node_type: str                      # 节点类型: action_node, chance_node, terminal
    player: Optional[int]               # 当前玩家 (0 或 1，chance节点为None)
    
    # 策略信息 (仅action_node有)
    available_actions: List[str]        # 可用动作列表
    hand: Optional[str] = None          # 手牌组合，如 "AhKs"
    strategy_probs: Optional[List[float]] = None  # 对应每个动作的概率
    
    # 发牌信息 (仅chance_node有)
    deal_card: Optional[str] = None     # 发出的牌
    deal_number: Optional[int] = None   # 发牌数量


class CFRTreeParser:
    """
    CFR策略树解析器
    遍历整个树结构，生成所有可能的决策路径
    """
    
    def __init__(self, json_path: str):
        """
        初始化解析器
        
        Args:
            json_path: JSON文件路径
        """
        self.json_path = Path(json_path)
        self.data = None
        self.total_lines = 0
        
    def load(self) -> None:
        """加载JSON文件"""
        print(f"正在加载文件: {self.json_path}")
        with open(self.json_path, 'r', encoding='utf-8') as f:
            self.data = json.load(f)
        print("文件加载完成!")
        
    def parse_all_lines(self) -> Generator[ActionLine, None, None]:
        """
        解析所有数据线 (生成器模式，节省内存)
        
        Yields:
            ActionLine: 每条决策路径
        """
        if self.data is None:
            self.load()
            
        yield from self._traverse_node(self.data, [], 0)
        
    def _traverse_node(
        self, 
        node: Dict[str, Any], 
        path_actions: List[str],
        depth: int
    ) -> Generator[ActionLine, None, None]:
        """
        递归遍历节点
        
        Args:
            node: 当前节点
            path_actions: 到达当前节点的动作路径
            depth: 当前深度
        """
        node_type = node.get('node_type', 'unknown')
        
        if node_type == 'action_node':
            yield from self._handle_action_node(node, path_actions, depth)
            
        elif node_type == 'chance_node':
            yield from self._handle_chance_node(node, path_actions, depth)
            
        else:
            # 终端节点或未知节点
            yield from self._handle_terminal_node(node, path_actions, depth)
            
    def _handle_action_node(
        self, 
        node: Dict[str, Any], 
        path_actions: List[str],
        depth: int
    ) -> Generator[ActionLine, None, None]:
        """处理action_node节点"""
        
        player = node.get('player')
        actions = node.get('actions', [])
        strategy_info = node.get('strategy', {})
        childrens = node.get('childrens', {})
        
        # 获取策略详情
        strategy_actions = strategy_info.get('actions', [])
        strategy_dict = strategy_info.get('strategy', {})
        
        # 为每个手牌组合生成一条数据线
        for hand, probs in strategy_dict.items():
            line = ActionLine(
                path=" -> ".join(path_actions) if path_actions else "ROOT",
                path_actions=path_actions.copy(),
                depth=depth,
                node_type='action_node',
                player=player,
                available_actions=actions,
                hand=hand,
                strategy_probs=probs
            )
            yield line
            self.total_lines += 1
            
        # 递归处理子节点
        for action, child in childrens.items():
            new_path = path_actions + [action]
            yield from self._traverse_node(child, new_path, depth + 1)
            
    def _handle_chance_node(
        self, 
        node: Dict[str, Any], 
        path_actions: List[str],
        depth: int
    ) -> Generator[ActionLine, None, None]:
        """处理chance_node节点"""
        
        deal_number = node.get('deal_number', 0)
        dealcards = node.get('dealcards', {})
        
        # 如果没有子节点（dealcards），这是一个终端chance节点
        if not dealcards:
            line = ActionLine(
                path=" -> ".join(path_actions) if path_actions else "ROOT",
                path_actions=path_actions.copy(),
                depth=depth,
                node_type='chance_node',
                player=None,
                available_actions=[],
                deal_number=deal_number
            )
            yield line
            self.total_lines += 1
            return
            
        # 为每张可能的牌递归处理
        for card, child in dealcards.items():
            # 如果子节点是简单的chance_node标记，生成终端线
            if child.get('deal_number', -1) == 0 and not child.get('dealcards') and not child.get('childrens'):
                line = ActionLine(
                    path=" -> ".join(path_actions + [f"DEAL:{card}"]),
                    path_actions=path_actions + [f"DEAL:{card}"],
                    depth=depth + 1,
                    node_type='chance_node_terminal',
                    player=None,
                    available_actions=[],
                    deal_card=card,
                    deal_number=0
                )
                yield line
                self.total_lines += 1
            else:
                # 递归处理有内容的子节点
                new_path = path_actions + [f"DEAL:{card}"]
                yield from self._traverse_node(child, new_path, depth + 1)
                
    def _handle_terminal_node(
        self, 
        node: Dict[str, Any], 
        path_actions: List[str],
        depth: int
    ) -> Generator[ActionLine, None, None]:
        """处理终端节点"""
        
        line = ActionLine(
            path=" -> ".join(path_actions) if path_actions else "ROOT",
            path_actions=path_actions.copy(),
            depth=depth,
            node_type='terminal',
            player=None,
            available_actions=[]
        )
        yield line
        self.total_lines += 1
        

class DataLineExporter:
    """
    数据线导出器
    支持多种输出格式
    """
    
    @staticmethod
    def to_csv(lines: Generator[ActionLine, None, None], output_path: str, max_lines: int = None) -> int:
        """
        导出为CSV格式
        
        Args:
            lines: 数据线生成器
            output_path: 输出文件路径
            max_lines: 最大导出行数（None为不限制）
            
        Returns:
            导出的行数
        """
        count = 0
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = None
            
            for line in lines:
                line_dict = asdict(line)
                # 将列表转换为字符串以便CSV存储
                line_dict['path_actions'] = '|'.join(line_dict['path_actions'])
                line_dict['available_actions'] = '|'.join(line_dict['available_actions']) if line_dict['available_actions'] else ''
                line_dict['strategy_probs'] = '|'.join(map(str, line_dict['strategy_probs'])) if line_dict['strategy_probs'] else ''
                
                if writer is None:
                    writer = csv.DictWriter(f, fieldnames=line_dict.keys())
                    writer.writeheader()
                    
                writer.writerow(line_dict)
                count += 1
                
                if count % 100000 == 0:
                    print(f"已导出 {count:,} 条数据线...")
                    
                if max_lines and count >= max_lines:
                    print(f"达到最大行数限制: {max_lines}")
                    break
                    
        print(f"CSV导出完成，共 {count:,} 条数据线 -> {output_path}")
        return count
        
    @staticmethod
    def to_jsonl(lines: Generator[ActionLine, None, None], output_path: str, max_lines: int = None) -> int:
        """
        导出为JSON Lines格式
        
        Args:
            lines: 数据线生成器
            output_path: 输出文件路径
            max_lines: 最大导出行数
            
        Returns:
            导出的行数
        """
        count = 0
        with open(output_path, 'w', encoding='utf-8') as f:
            for line in lines:
                f.write(json.dumps(asdict(line), ensure_ascii=False) + '\n')
                count += 1
                
                if count % 100000 == 0:
                    print(f"已导出 {count:,} 条数据线...")
                    
                if max_lines and count >= max_lines:
                    print(f"达到最大行数限制: {max_lines}")
                    break
                    
        print(f"JSONL导出完成，共 {count:,} 条数据线 -> {output_path}")
        return count


def parse_config(config_path: str) -> Dict[str, Any]:
    """
    从配置文件中解析 board 和 range 信息
    
    Args:
        config_path: 配置文件路径
        
    Returns:
        Dict: {
            'board': str,  # 如 "Ah,Kd,9s"
            'ip_range': {手牌: 概率},
            'oop_range': {手牌: 概率}
        }
    """
    board = ""
    ip_range = {}
    oop_range = {}
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('set_board'):
                    # 提取 board 信息，如 "set_board 7h,3d,2c"
                    board = line.replace('set_board', '').strip()
                elif line.startswith('set_range_ip'):
                    # 解析IP范围: set_range_ip AA:1.000,AKs:1.000,...
                    range_str = line.replace('set_range_ip', '').strip()
                    ip_range = _parse_range_string(range_str)
                elif line.startswith('set_range_oop'):
                    # 解析OOP范围
                    range_str = line.replace('set_range_oop', '').strip()
                    oop_range = _parse_range_string(range_str)
    except FileNotFoundError:
        print(f"警告: 配置文件 {config_path} 不存在")
    except Exception as e:
        print(f"警告: 解析配置文件时出错: {e}")
    
    return {
        'board': board,
        'ip_range': ip_range,
        'oop_range': oop_range
    }

def parse_range_from_config(config_path: str) -> Dict[str, Dict[str, float]]:
    """
    从配置文件中解析preflop range信息（兼容旧接口）
    
    Args:
        config_path: 配置文件路径
        
    Returns:
        Dict: {
            'ip': {手牌: 概率},
            'oop': {手牌: 概率}
        }
    """
    config_data = parse_config(config_path)
    return {
        'ip': config_data['ip_range'],
        'oop': config_data['oop_range']
    }


def _parse_range_string(range_str: str) -> Dict[str, float]:
    """
    解析range字符串，如 "AA:1.000,AKs:1.000,AKo:0.5"
    
    Returns:
        Dict: {手牌: 概率}
    """
    result = {}
    if not range_str:
        return result
    
    # 分割手牌
    parts = range_str.split(',')
    for part in parts:
        part = part.strip()
        if ':' in part:
            hand, prob_str = part.rsplit(':', 1)
            try:
                prob = float(prob_str)
                result[hand.strip()] = prob
            except ValueError:
                pass
        else:
            # 没有概率，默认为1.0
            result[part.strip()] = 1.0
    
    return result


def expand_hand_type_to_combos(hand_type: str, board: List[str] = None) -> List[str]:
    """
    将手牌类型展开为所有具体的手牌组合
    
    Args:
        hand_type: 手牌类型，如 "AA", "AKs", "AKo", "Q4s", "AhKs"
        board: 公共牌列表（可选），用于排除与公共牌冲突的组合
        
    Returns:
        具体手牌组合列表，如 ["AhAs", "AcAd", ...] 或 ["Qc4c", "Qd4d", ...]
        
    Examples:
        >>> expand_hand_type_to_combos("AA")
        ['AcAd', 'AcAh', 'AcAs', 'AdAh', 'AdAs', 'AhAs']
        >>> expand_hand_type_to_combos("AKs")
        ['AcKc', 'AdKd', 'AhKh', 'AsKs']
        >>> expand_hand_type_to_combos("Q4s")
        ['Qc4c', 'Qd4d', 'Qh4h', 'Qs4s']
    """
    suits = ['c', 'd', 'h', 's']  # 梅花、方块、红心、黑桃
    combos = []
    
    # 解析公共牌（如果有的话）
    board_cards = set()
    if board:
        for card in board:
            card = card.strip()
            if len(card) >= 2:
                board_cards.add(card)
    
    hand_type = hand_type.strip()
    hand_len = len(hand_type)
    
    def card_conflicts_with_board(card: str) -> bool:
        """检查手牌是否与公共牌冲突"""
        return card in board_cards
    
    def combo_conflicts_with_board(card1: str, card2: str) -> bool:
        """检查手牌组合是否与公共牌冲突"""
        return card_conflicts_with_board(card1) or card_conflicts_with_board(card2)
    
    if hand_len == 4:
        # 具体手牌，如 "AhKs"
        combos.append(hand_type)
        
    elif hand_len == 3:
        rank1 = hand_type[0]
        rank2 = hand_type[1]
        suffix = hand_type[2].lower()
        
        if suffix == 's':
            # 同花 (suited): Q4s -> Qc4c, Qd4d, Qh4h, Qs4s
            if rank1 == rank2:
                raise ValueError(f"{rank1}{rank2}s is not a valid hand (pairs cannot be suited)")
            for suit in suits:
                card1 = f"{rank1}{suit}"
                card2 = f"{rank2}{suit}"
                if not combo_conflicts_with_board(card1, card2):
                    combos.append(f"{card1}{card2}")
                    
        elif suffix == 'o':
            # 杂色 (offsuit): AKo -> 所有不同花色的组合
            for i, suit1 in enumerate(suits):
                for j, suit2 in enumerate(suits):
                    if suit1 != suit2:
                        card1 = f"{rank1}{suit1}"
                        card2 = f"{rank2}{suit2}"
                        if not combo_conflicts_with_board(card1, card2):
                            combos.append(f"{card1}{card2}")
        else:
            raise ValueError(f"Invalid hand type suffix: {suffix}")
            
    elif hand_len == 2:
        rank1 = hand_type[0]
        rank2 = hand_type[1]
        
        if rank1 == rank2:
            # 口袋对 (pair): AA -> AcAd, AcAh, AcAs, AdAh, AdAs, AhAs
            for i, suit1 in enumerate(suits):
                for j, suit2 in enumerate(suits):
                    if j > i:  # 避免重复，如 AcAd 和 AdAc
                        card1 = f"{rank1}{suit1}"
                        card2 = f"{rank2}{suit2}"
                        if not combo_conflicts_with_board(card1, card2):
                            combos.append(f"{card1}{card2}")
        else:
            # 无标记的两张不同牌: AK -> 所有组合（suited + offsuit）
            for i, suit1 in enumerate(suits):
                for j, suit2 in enumerate(suits):
                    card1 = f"{rank1}{suit1}"
                    card2 = f"{rank2}{suit2}"
                    if not combo_conflicts_with_board(card1, card2):
                        combos.append(f"{card1}{card2}")
    else:
        raise ValueError(f"Invalid hand type format: {hand_type}")
    
    return combos


def _expand_range_to_hands(range_dict: Dict[str, float], board: List[str] = None) -> Dict[str, float]:
    """
    将range字典扩展为所有手牌的组合
    例如：AA -> 所有AA组合，AKs -> 所有AK同花组合
    
    Args:
        range_dict: {手牌类型: 概率}，如 {"AA": 1.0, "AKs": 0.5}
        board: 公共牌列表（可选），用于排除与公共牌冲突的组合
        
    Returns:
        {具体手牌: 概率}，如 {"AhAs": 1.0, "AcKc": 0.5, ...}
    """
    result = {}
    
    for hand_type, prob in range_dict.items():
        try:
            combos = expand_hand_type_to_combos(hand_type, board)
            for combo in combos:
                result[combo] = prob
        except ValueError:
            # 如果无法展开，直接使用原始手牌（可能已经是具体手牌）
            result[hand_type] = prob
    
    return result


class StrategyOnlyParser:
    """
    仅解析策略信息的精简解析器
    每条数据线包含：路径 + 手牌 + 策略概率
    这是最常用的输出格式
    """
    
    def __init__(self, json_path: str, config_path: Optional[str] = None):
        """
        初始化解析器
        
        Args:
            json_path: JSON文件路径
            config_path: 配置文件路径（可选，用于读取初始preflop range和board）
        """
        self.json_path = Path(json_path)
        self.config_path = Path(config_path) if config_path else None
        self.data = None
        self.initial_ranges = {'ip': {}, 'oop': {}}
        self.board = ""  # 公共牌信息
        
        # 如果提供了配置文件，解析初始range和board
        if self.config_path and self.config_path.exists():
            config_data = parse_config(str(self.config_path))
            self.board = config_data['board']
            
            # 将公共牌字符串解析为列表
            board_list = [card.strip() for card in self.board.split(',') if card.strip()] if self.board else []
            
            # 展开初始 range（从手牌类型如 "AA", "AKs" 转换为具体组合如 "AhAs", "AcKc"）
            ip_range_raw = config_data['ip_range']
            oop_range_raw = config_data['oop_range']
            
            self.initial_ranges = {
                'ip': _expand_range_to_hands(ip_range_raw, board_list),
                'oop': _expand_range_to_hands(oop_range_raw, board_list)
            }
            
            print(f"已从配置文件加载: Board={self.board}")
            print(f"  IP: {len(ip_range_raw)}手牌类型 -> {len(self.initial_ranges['ip'])}具体组合")
            print(f"  OOP: {len(oop_range_raw)}手牌类型 -> {len(self.initial_ranges['oop'])}具体组合")
        
    def load(self) -> None:
        print(f"正在加载文件: {self.json_path}")
        with open(self.json_path, 'r', encoding='utf-8') as f:
            self.data = json.load(f)
        print("文件加载完成!")
        
    def parse(self) -> Generator[Dict[str, Any], None, None]:
        """
        解析策略数据线
        
        Yields:
            Dict: {
                'path': 动作路径,
                'player': 玩家 (0=OOP, 1=IP),
                'hand': 手牌,
                'actions': 可用动作列表,
                'probs': 策略概率列表,
                'action_probs': {动作: 概率} 字典,
                'ip_range': IP玩家的手牌范围 (reach_probs，{手牌: 概率}),
                'oop_range': OOP玩家的手牌范围 (reach_probs，{手牌: 概率}),
                'ev': 期望价值 (可选)
            }
            
        Note:
            - ip_range 和 oop_range 表示双方在当前节点的手牌范围（reach probabilities）
            - 当前行动玩家的 range 从当前节点的 ranges 字段获取
            - 对手的 range 从父节点传递（即对手上一次行动时的 range）
            - 这样在每个节点都能同时看到双方的手牌范围变化
        """
        if self.data is None:
            self.load()
        
        # 初始化range：从config读取的初始preflop range
        # 注意：初始range是手牌类型（如AA, AKs），需要转换为具体手牌
        # 但CFR的strategy已经包含所有手牌，所以这里主要用于追踪
        initial_ip_range = self.initial_ranges['ip'].copy()
        initial_oop_range = self.initial_ranges['oop'].copy()
        
        yield from self._traverse(self.data, [], initial_ip_range, initial_oop_range)
        
    def _traverse(
        self, 
        node: Dict[str, Any], 
        path: List[str],
        current_ip_range: Optional[Dict[str, float]] = None,
        current_oop_range: Optional[Dict[str, float]] = None
    ) -> Generator[Dict[str, Any], None, None]:
        """
        递归遍历，追踪range的变化
        
        Range 追踪逻辑：
        - C++ 侧在每个 ACTION 节点导出了当前行动玩家的 range（reach_probs）
        - 这里需要同时追踪双方的 range：
          - 当前行动玩家的 range：从当前节点的 ranges 字段获取
          - 对手的 range：从父节点传递下来（对手上一次行动时的 range）
        
        Args:
            node: 当前节点
            path: 到达当前节点的路径
            current_ip_range: 当前IP玩家的range（从父节点传递，Dict[手牌, 概率]）
            current_oop_range: 当前OOP玩家的range（从父节点传递，Dict[手牌, 概率]）
        """
        node_type = node.get('node_type', '')
        
        if node_type == 'action_node':
            player = node.get('player')
            actions = node.get('actions', [])
            strategy_info = node.get('strategy', {})
            strategy_dict = strategy_info.get('strategy', {})
            
            # 提取EV信息（按照GitHub issue建议，evs在action_node根级别）
            # evs结构: {"actions": [...], "evs": {hand: [ev1, ev2, ...]}}
            evs_info = node.get('evs', {})
            evs_dict = evs_info.get('evs', {}) if isinstance(evs_info, dict) else {}
            
            # 提取Equity信息
            # equities结构: {"actions": [...], "equities": {hand: [equity1, equity2, ...]}}
            equities_info = node.get('equities', {})
            equities_dict = equities_info.get('equities', {}) if isinstance(equities_info, dict) else {}
            
            # 提取 Range 信息（C++ 侧导出双方的 ranges）
            # ranges结构: {"player": 当前行动玩家, "ip_range": {...}, "oop_range": {...}}
            ranges_info = node.get('ranges', {})
            
            # 直接从 JSON 读取双方的 range（C++ 已经计算好了）
            # 如果 JSON 中没有，则使用配置文件的初始值或从父节点继承
            if isinstance(ranges_info, dict):
                # 优先使用 JSON 中的 range
                ip_range = ranges_info.get('ip_range', {})
                oop_range = ranges_info.get('oop_range', {})
                
                # 如果 JSON 中没有，使用父节点传递的值
                if not ip_range and current_ip_range:
                    ip_range = current_ip_range.copy()
                if not oop_range and current_oop_range:
                    oop_range = current_oop_range.copy()
                    
                # 如果仍然没有，且是 ROOT 节点，使用配置文件的初始值
                if not path:
                    if not ip_range and self.initial_ranges.get('ip'):
                        ip_range = self.initial_ranges['ip'].copy()
                    if not oop_range and self.initial_ranges.get('oop'):
                        oop_range = self.initial_ranges['oop'].copy()
            else:
                # 回退：使用父节点的值或初始值
                ip_range = current_ip_range.copy() if current_ip_range else {}
                oop_range = current_oop_range.copy() if current_oop_range else {}
                if not path:
                    if not ip_range and self.initial_ranges.get('ip'):
                        ip_range = self.initial_ranges['ip'].copy()
                    if not oop_range and self.initial_ranges.get('oop'):
                        oop_range = self.initial_ranges['oop'].copy()
            
            # 生成策略数据线（每个手牌一行）
            for hand, hand_data in strategy_dict.items():
                # 处理手牌数据：可能是列表（概率）或字典（包含更多信息）
                if isinstance(hand_data, dict):
                    # 如果手牌数据是字典，可能包含概率、EV等信息
                    probs = hand_data.get('probs', hand_data.get('prob', hand_data.get('strategy', [])))
                    hand_ev = hand_data.get('ev', hand_data.get('EV', hand_data.get('value')))
                elif isinstance(hand_data, list):
                    # 如果手牌数据是列表，就是概率列表
                    probs = hand_data
                    hand_ev = None
                else:
                    # 其他情况，尝试转换
                    probs = [hand_data] if isinstance(hand_data, (int, float)) else []
                    hand_ev = None
                
                # 从evs_dict中提取该手牌的EV（如果存在）
                hand_ev_from_evs = None
                if evs_dict and hand in evs_dict:
                    ev_values = evs_dict[hand]
                    if isinstance(ev_values, list) and len(ev_values) == len(actions):
                        # EV值对应每个动作
                        hand_ev_from_evs = dict(zip(actions, ev_values))
                    elif isinstance(ev_values, (int, float)):
                        # 单个EV值
                        hand_ev_from_evs = ev_values
                
                # EV优先级：手牌级别 > evs_dict
                ev = hand_ev or hand_ev_from_evs
                
                # 从equities_dict中提取该手牌的Equity（如果存在）
                equity = None
                if equities_dict and hand in equities_dict:
                    equity_values = equities_dict[hand]
                    if isinstance(equity_values, list) and len(equity_values) == len(actions):
                        # Equity值对应每个动作
                        equity = dict(zip(actions, equity_values))
                    elif isinstance(equity_values, (int, float)):
                        # 单个Equity值
                        equity = equity_values
                
                yield {
                    'path': ' -> '.join(path) if path else 'ROOT',
                    'player': player,
                    'hand': hand,
                    'actions': actions,
                    'probs': probs,
                    'action_probs': dict(zip(actions, probs)) if probs else {},
                    'ip_range': ip_range,  # IP range：当前状态下 IP 玩家的 reach_probs
                    'oop_range': oop_range,  # OOP range：当前状态下 OOP 玩家的 reach_probs
                    'ev': ev,
                    'equity': equity
                }
            
            # 递归子节点：传递当前的 range
            # C++ 侧已经在每个节点导出了双方的 range，Python 只需要读取
            for action, child in node.get('childrens', {}).items():
                yield from self._traverse(child, path + [action], ip_range, oop_range)
                
        elif node_type == 'chance_node':
            # 处理发牌节点：range保持不变，传递给子节点
            for card, child in node.get('dealcards', {}).items():
                if child.get('childrens') or child.get('dealcards'):
                    yield from self._traverse(child, path + [f'DEAL:{card}'], current_ip_range, current_oop_range)
    


def analyze_tree_stats(json_path: str) -> Dict[str, Any]:
    """
    分析树结构的统计信息
    
    Args:
        json_path: JSON文件路径
        
    Returns:
        统计信息字典
    """
    print("正在分析树结构...")
    
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    stats = {
        'total_action_nodes': 0,
        'total_chance_nodes': 0,
        'total_strategies': 0,
        'max_depth': 0,
        'unique_hands': set(),
        'unique_actions': set(),
        'paths_sample': []
    }
    
    def traverse(node, depth=0, path=''):
        stats['max_depth'] = max(stats['max_depth'], depth)
        node_type = node.get('node_type', '')
        
        if node_type == 'action_node':
            stats['total_action_nodes'] += 1
            actions = node.get('actions', [])
            stats['unique_actions'].update(actions)
            
            strategy_dict = node.get('strategy', {}).get('strategy', {})
            stats['total_strategies'] += len(strategy_dict)
            stats['unique_hands'].update(strategy_dict.keys())
            
            if len(stats['paths_sample']) < 10:
                stats['paths_sample'].append(path or 'ROOT')
                
            for action, child in node.get('childrens', {}).items():
                new_path = f"{path} -> {action}" if path else action
                traverse(child, depth + 1, new_path)
                
        elif node_type == 'chance_node':
            stats['total_chance_nodes'] += 1
            for card, child in node.get('dealcards', {}).items():
                new_path = f"{path} -> DEAL:{card}" if path else f"DEAL:{card}"
                traverse(child, depth + 1, new_path)
    
    traverse(data)
    
    # 转换set为list以便打印
    stats['unique_hands'] = len(stats['unique_hands'])
    stats['unique_actions'] = list(stats['unique_actions'])
    
    return stats


def main():
    """主函数 - 演示用法"""
    
    json_path = 'river_strategy.json'
    
    # 1. 先分析树结构统计
    print("=" * 60)
    print("步骤1: 分析树结构")
    print("=" * 60)
    stats = analyze_tree_stats(json_path)
    print(f"\n统计信息:")
    print(f"  - Action节点数: {stats['total_action_nodes']:,}")
    print(f"  - Chance节点数: {stats['total_chance_nodes']:,}")
    print(f"  - 策略记录总数: {stats['total_strategies']:,}")
    print(f"  - 不同手牌组合: {stats['unique_hands']}")
    print(f"  - 最大深度: {stats['max_depth']}")
    print(f"  - 可用动作: {stats['unique_actions']}")
    print(f"  - 路径示例: {stats['paths_sample'][:5]}")
    
    # 2. 使用精简解析器导出策略数据
    print("\n" + "=" * 60)
    print("步骤2: 导出策略数据 (精简格式)")
    print("=" * 60)
    
    # 尝试找到对应的配置文件
    config_path = None
    json_stem = Path(json_path).stem
    
    # 尝试多种配置文件命名规则
    possible_configs = [
        # result_XX_XXX.json -> configs/config_XX_XXX.txt
        Path('configs') / (json_stem.replace('result_', 'config_') + '.txt'),
        # river_strategy.json -> solver/configs/river.txt
        Path('solver/configs') / (json_stem.replace('_strategy', '') + '.txt'),
        # river_strategy.json -> configs/river.txt
        Path('configs') / (json_stem.replace('_strategy', '') + '.txt'),
        # 直接匹配 json_stem.txt
        Path('solver/configs') / (json_stem + '.txt'),
        Path('configs') / (json_stem + '.txt'),
    ]
    
    for possible_config in possible_configs:
        if possible_config.exists():
            config_path = str(possible_config)
            print(f"找到配置文件: {config_path}")
            break
    
    if not config_path:
        print("未找到对应的配置文件，将不包含初始range信息")
    
    parser = StrategyOnlyParser(json_path, config_path=config_path)
    
    # 导出为CSV（使用固定字段，避免动态列问题）
    output_csv = 'river_lines.csv'
    count = 0
    fieldnames = ['path', 'player', 'hand', 'actions', 'probs', 'board', 'ip_range', 'oop_range', 'ev', 'equity']
    
    with open(output_csv, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        
        for line in parser.parse():
            # 处理ip_range和oop_range：如果是字典或列表，转换为字符串
            ip_range_str = None
            oop_range_str = None
            ev_str = None
            equity_str = None
            
            if line.get('ip_range') is not None:
                if isinstance(line['ip_range'], (dict, list)):
                    ip_range_str = json.dumps(line['ip_range'], ensure_ascii=False)
                else:
                    ip_range_str = str(line['ip_range'])
            
            if line.get('oop_range') is not None:
                if isinstance(line['oop_range'], (dict, list)):
                    oop_range_str = json.dumps(line['oop_range'], ensure_ascii=False)
                else:
                    oop_range_str = str(line['oop_range'])
            
            if line.get('ev') is not None:
                if isinstance(line['ev'], (dict, list)):
                    ev_str = json.dumps(line['ev'], ensure_ascii=False)
                else:
                    ev_str = str(line['ev'])
            
            if line.get('equity') is not None:
                if isinstance(line['equity'], (dict, list)):
                    equity_str = json.dumps(line['equity'], ensure_ascii=False)
                else:
                    equity_str = str(line['equity'])
            
            row = {
                'path': line['path'],
                'player': line['player'],
                'hand': line['hand'],
                'actions': '|'.join(line['actions']),
                'probs': '|'.join(f"{p:.3f}" for p in line['probs']),
                'board': parser.board,  # 添加 board 列
                'ip_range': ip_range_str,
                'oop_range': oop_range_str,
                'ev': ev_str,
                'equity': equity_str
            }
            writer.writerow(row)
            count += 1
            
            if count % 100000 == 0:
                print(f"  已导出 {count:,} 条...")
                
    print(f"\n[OK] 导出完成: {output_csv}")
    print(f"  总计 {count:,} 条策略数据线")
    
    
    print(f"\n[OK] 所有导出完成!")


if __name__ == '__main__':
    main()

