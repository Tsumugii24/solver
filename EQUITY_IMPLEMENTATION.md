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

两者都是通过递归传递计算的，地位完全对等。

---

## 实现方案

### 方案选择：精确递归传递

选择从叶子节点精确计算 equity，然后递归传递到父节点，而不是从 EV 反推。

**原因**：
1. EV 反推需要知道 payoff 结构，非叶子节点无法准确反推
2. 递归传递与 EV 计算逻辑一致，代码结构清晰
3. 对于 Action/Chance 节点，equity 就是子节点 equity 的加权平均

### 各节点类型的 Equity 计算

| 节点类型 | Equity 计算方式 |
|---------|----------------|
| **Showdown** | `win_prob + (1 - win_prob - loss_prob) / 2` |
| **Terminal (对手 fold)** | `1.0` |
| **Terminal (我方 fold)** | `0.0` |
| **Chance** | `Σ(child_equity)` 子节点求和 |
| **Action (当前玩家)** | `Σ(strategy × child_equity)` 策略加权 |
| **Action (对手)** | `Σ(child_equity)` 直接求和 |

### Showdown 节点的 tie_prob 计算

采用**反推方案**而非 Hash Map 方案：

```cpp
tie_prob = 1 - win_prob - loss_prob
```

**优势**：
- 内存：~10 KB vs ~1.5 MB (Hash Map)
- 无需额外预处理遍历
- Cache 友好（连续数组访问）

---

## 代码改动

### 1. 新增 CfrResult 结构体

**文件**: `include/solver/PCfrSolver.h`

```cpp
struct CfrResult {
    vector<float> payoffs;
    vector<float> equity;
    
    CfrResult() = default;
    CfrResult(size_t size) : payoffs(size, 0.0f), equity(size, 0.0f) {}
    CfrResult(vector<float> p, vector<float> e) : payoffs(std::move(p)), equity(std::move(e)) {}
};
```

### 2. 修改递归函数返回类型

**文件**: `include/solver/PCfrSolver.h`, `src/solver/PCfrSolver.cpp`

| 函数 | 原返回类型 | 新返回类型 |
|-----|-----------|-----------|
| `cfr()` | `vector<float>` | `CfrResult` |
| `actionUtility()` | `vector<float>` | `CfrResult` |
| `showdownUtility()` | `vector<float>` | `CfrResult` |
| `terminalUtility()` | `vector<float>` | `CfrResult` |
| `chanceUtility()` | `vector<float>` | `CfrResult` |

### 3. Trainable 接口扩展

**文件**: `include/trainable/Trainable.h`

```cpp
virtual void setEquity(const vector<float>& equity) = 0;
virtual json dump_equity() = 0;
```

### 4. Trainable 实现（三个版本）

**文件**: 
- `include/trainable/DiscountedCfrTrainable.h` / `.cpp`
- `include/trainable/DiscountedCfrTrainableSF.h` / `.cpp`
- `include/trainable/DiscountedCfrTrainableHF.h` / `.cpp`

新增成员变量和方法：
```cpp
vector<float> equity;  // 或 vector<EvsStorage> equity (HF/SF版本)

void setEquity(const vector<float>& equity);
json dump_equity();
```

### 5. showdownUtility 实现

**文件**: `src/solver/PCfrSolver.cpp`

```cpp
// 正向循环：计算 win 并存储
effective_winsum_arr[idx] = effective_winsum;
effective_total_arr[idx] = effective_total;

// 反向循环：计算 loss，然后反推 tie
float win_prob = effective_winsum_arr[idx] / effective_total;
float loss_prob = effective_losssum / effective_total;
float tie_prob = 1.0f - win_prob - loss_prob;
equity[idx] = win_prob + tie_prob * 0.5f;
```

### 6. actionUtility 实现

**文件**: `src/solver/PCfrSolver.cpp`

```cpp
// 收集子节点 equity
vector<float>& action_equity = results[action_id].equity;
all_action_equity[action_id] = action_equity;

// 策略加权累加
total_equity[hand_id] += strategy_prob * action_equity[hand_id];

// 存储到 Trainable
trainable->setEquity(equity_to_store);
```

### 7. JSON 导出

**文件**: `src/solver/PCfrSolver.cpp` (reConvertJson 函数)

```cpp
(*retval)["equity"] = trainable->dump_equity();

// 处理花色交换
if((*retval)["equity"].contains("equity")) {
    this->exchangeRange((*retval)["equity"]["equity"], rank1, rank2, one_node);
}
```

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
  "equity": {
    "actions": ["FOLD", "CALL", "RAISE:100"],
    "equity": {
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
| `src/solver/PCfrSolver.cpp` | 修改 5 个函数实现 + 导出逻辑 |
| `include/trainable/Trainable.h` | 新增接口 |
| `include/trainable/DiscountedCfrTrainable.h` | 新增成员和方法声明 |
| `include/trainable/DiscountedCfrTrainableSF.h` | 新增成员和方法声明 |
| `include/trainable/DiscountedCfrTrainableHF.h` | 新增成员和方法声明 |
| `src/trainable/DiscountedCfrTrainable.cpp` | 实现 setEquity/dump_equity |
| `src/trainable/DiscountedCfrTrainableSF.cpp` | 实现 setEquity/dump_equity |
| `src/trainable/DiscountedCfrTrainableHF.cpp` | 实现 setEquity/dump_equity |
