# Equity 实现方案

## 核心概念

### Equity 定义

```
equity = win_prob + tie_prob / 2
```

- **赢 (win)**: 算 1.0
- **平局 (tie)**: 算 0.5
- **输 (loss)**: 算 0.0

### 与 EV 的关系

| 指标 | 含义 | 单位 |
|-----|------|-----|
| **EV** | 期望收益 | 筹码数 (chips) |
| **Equity** | 胜率期望 | 概率 (0-1) |

两者的计算逻辑**完全一致**：
1. CFR 递归中：使用 counterfactual value 形式
2. 存储/导出时：用 `rp_sum` 归一化

---

## 实现方案 B（最终方案）

### 核心思想

**Equity 和 EV 使用完全相同的处理方式**：

1. **CFR 递归中**：使用 counterfactual value 形式（不标准化），直接累加
2. **存储时**：用 `rp_sum`（对手的 effective_total，考虑 blocker）归一化

### 为什么这样设计？

EV 的处理方式：
```cpp
// CFR 递归中：counterfactual value，直接累加
payoffs[hand_id] += action_utilities[hand_id];

// 存储时：用 rp_sum 归一化
evs[idx] = (rp_sum > 0) ? one_ev / rp_sum : 0;
```

Equity 完全一致：
```cpp
// CFR 递归中：counterfactual value，直接累加
total_equity[hand_id] += results[action_id].equity[hand_id];

// 存储时：用 rp_sum 归一化
equity_to_store[idx] = (rp_sum > 0) ? one_equity / rp_sum : 0;
```

### 优势

- **完全精确**：没有 blocker 近似误差
- **计算高效**：几乎没有额外开销（只是在存储时多一次除法）
- **代码一致**：和 EV 的处理逻辑完全相同，易于维护

---

## 各节点类型的 Equity 计算

### 叶子节点（Counterfactual Value 形式）

| 节点类型 | Equity 计算方式 |
|---------|----------------|
| **Showdown** | `effective_winsum + 0.5 * effective_tiesum` |
| **Terminal (对手 fold)** | `effective_oppo_reach` |
| **Terminal (我方 fold)** | `0` |

### 非叶子节点（直接累加）

| 节点类型 | Equity 计算方式 |
|---------|----------------|
| **Chance** | `Σ(child_equity)` 直接累加 |
| **Action (当前玩家)** | `Σ(strategy × child_equity)` 策略加权 |
| **Action (对手)** | `Σ(child_equity)` 直接累加 |

### 存储时归一化

```cpp
// rp_sum = 对手的 effective_total（排除 blocker）
float rp_sum = oppo_sum - oppo_card_sum[card1] - oppo_card_sum[card2] + plus_reach_prob;

// 归一化（和 EV 完全一样）
equity_to_store[idx] = (rp_sum > 0) ? one_equity / rp_sum : 0;
```

---

## 代码实现

### 1. Showdown 节点

**文件**: `src/solver/PCfrSolver.cpp`

```cpp
// 方案 B：计算 counterfactual equity（不标准化，和 EV 一致）
// equity = effective_winsum + 0.5 * effective_tiesum
if(this->enable_equity) {
    int idx = one_player_comb.reach_prob_index;
    float effective_winsum = effective_winsum_arr[idx];
    float effective_total = effective_total_arr[idx];
    float effective_tiesum = effective_total - effective_winsum - effective_losssum;
    if(effective_tiesum < 0) effective_tiesum = 0;  // 防止浮点误差
    equity[idx] = effective_winsum + 0.5f * effective_tiesum;
}
```

### 2. Terminal 节点

**文件**: `src/solver/PCfrSolver.cpp`

```cpp
// 方案 B：counterfactual equity
// 如果 player 赢（对手 fold）：equity = effective_oppo_reach
// 如果 player 输（player fold）：equity = 0
if(this->enable_equity) {
    equity[i] = (player_payoff > 0) ? effective_oppo_reach : 0.0f;
}
```

### 3. ACTION 节点

**文件**: `src/solver/PCfrSolver.cpp`

```cpp
// 方案 B：equity 和 payoffs 使用完全相同的累加逻辑
for (int hand_id = 0; hand_id < action_utilities.size(); hand_id++) {
    if (player == node->getPlayer()) {
        float strategy_prob = current_strategy[hand_id + action_id * node_player_private_cards.size()];
        payoffs[hand_id] += strategy_prob * action_utilities[hand_id];
        if(this->enable_equity && !results[action_id].equity.empty()) {
            total_equity[hand_id] += strategy_prob * results[action_id].equity[hand_id];
        }
    } else {
        payoffs[hand_id] += action_utilities[hand_id];
        // 方案 B：直接累加，和 payoffs 完全一致
        if(this->enable_equity && !results[action_id].equity.empty()) {
            total_equity[hand_id] += results[action_id].equity[hand_id];
        }
    }
}
```

### 4. CHANCE 节点

**文件**: `src/solver/PCfrSolver.cpp`

```cpp
// 方案 B：equity 和 payoffs 使用完全相同的累加逻辑（直接累加）
if(this->enable_equity && !child_result.equity.empty()) {
    for (int i = 0; i < child_result.equity.size(); i++) {
        chance_equity[i] += child_result.equity[i];
    }
}
```

### 5. 存储时归一化（关键）

**文件**: `src/solver/PCfrSolver.cpp`

```cpp
// 方案 B：用 rp_sum 归一化 equity（和 EV 完全一致）
if(this->enable_equity && !all_action_equity.empty()) {
    vector<float> equity_to_store(actions.size() * node_player_private_cards.size(), 0.0);
    for (std::size_t action_id = 0; action_id < actions.size(); action_id++) {
        if(all_action_equity[action_id].empty()) continue;
        for (std::size_t hand_id = 0; hand_id < node_player_private_cards.size(); hand_id++) {
            float one_equity = (all_action_equity)[action_id][hand_id];
            
            // 计算 rp_sum（和 EV 一样）
            int oppo_same_card_ind = this->pcm.indPlayer2Player(player, oppo, hand_id);
            float plus_reach_prob;
            const PrivateCards& one_player_hand = player_hand[hand_id];
            if(oppo_same_card_ind == -1){
                plus_reach_prob = 0;
            }else{
                plus_reach_prob = reach_probs[oppo_same_card_ind];
            }
            float rp_sum = (
                oppo_sum - oppo_card_sum[one_player_hand.card1]
                - oppo_card_sum[one_player_hand.card2]
                + plus_reach_prob);
            
            std::size_t idx = hand_id + action_id * node_player_private_cards.size();
            // 归一化 equity（和 EV 一样）
            equity_to_store[idx] = (rp_sum > 0) ? one_equity / rp_sum : 0;
        }
    }
    trainable->setEquity(equity_to_store);
}
```

---

## EV 与 Equity 对比

| 步骤 | EV | Equity |
|------|-----|--------|
| Showdown | `effective_winsum * win_payoff + effective_losssum * lose_payoff` | `effective_winsum + 0.5 * effective_tiesum` |
| Terminal (player 赢) | `player_payoff * effective_oppo_reach` | `effective_oppo_reach` |
| Terminal (player 输) | `player_payoff * effective_oppo_reach` | `0` |
| ACTION（己方） | `Σ(strategy × child_ev)` | `Σ(strategy × child_equity)` |
| ACTION（对手） | `Σ(child_ev)` 直接累加 | `Σ(child_equity)` 直接累加 |
| CHANCE | `Σ(child_ev)` 直接累加 | `Σ(child_equity)` 直接累加 |
| 存储时归一化 | `ev / rp_sum` | `equity / rp_sum` |

---

## 为什么不使用方案 A（标准化概率 + 边缘策略加权）？

### 方案 A 的问题

1. **ACTION 节点（对手决策）**：需要计算对手的边缘策略概率
   - 全局边缘策略：有 blocker 近似误差
   - 每个 player 手牌单独计算：O(player_hands × actions × oppo_hands) ≈ **1000 倍开销**

2. **CHANCE 节点**：需要计算有效发牌数
   - 统一的 valid_deal_count：有 blocker 近似误差
   - 每个 player 手牌单独计算：O(player_hands × cards) ≈ **1000 倍开销**

### 方案 B 的优势

| 方案 | 精确度 | 额外开销 |
|------|--------|----------|
| 方案 A（近似） | 有 blocker 误差 | 无 |
| 方案 A（精确） | 完全精确 | ~1000x |
| **方案 B** | **完全精确** | **~2x** |

方案 B 通过在存储时归一化（复用 EV 的 rp_sum 计算），实现了完全精确且高效的 equity 计算。

---

## 导出 JSON 格式

每个 Action 节点的 JSON 结构：

```json
{
  "actions": ["FOLD", "CALL", "RAISE:100"],
  "player": 0,
  "strategy": {
    "actions": ["FOLD", "CALL", "RAISE:100"],
    "strategy": {
      "AsKs": [0.0, 0.3, 0.7],
      "AsKh": [0.1, 0.4, 0.5]
    }
  },
  "evs": {
    "actions": ["FOLD", "CALL", "RAISE:100"],
    "evs": {
      "AsKs": [-50.0, 12.5, 25.0],
      "AsKh": [-50.0, 10.0, 20.0]
    }
  },
  "equities": {
    "actions": ["FOLD", "CALL", "RAISE:100"],
    "equities": {
      "AsKs": [0.0, 0.52, 0.58],
      "AsKh": [0.0, 0.48, 0.55]
    }
  }
}
```

---

## 修改文件清单

| 文件 | 改动类型 |
|-----|---------|
| `include/solver/PCfrSolver.h` | 新增 CfrResult，修改函数声明 |
| `src/solver/PCfrSolver.cpp` | 修改 5 个函数实现 + 存储归一化逻辑 |
| `include/trainable/Trainable.h` | 新增接口 |
| `include/trainable/DiscountedCfrTrainable.h` | 新增成员和方法声明 |
| `src/trainable/DiscountedCfrTrainable.cpp` | 实现 setEquity/dump_equities |
| `include/trainable/CfrPlusTrainable.h` | 新增成员和方法声明 |
| `src/trainable/CfrPlusTrainable.cpp` | 实现 setEquity/dump_equities |
