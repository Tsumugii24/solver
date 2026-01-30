# TexasSolver 配置参数文档

本文档详细说明了 TexasSolver 扑克求解器配置文件中各参数的含义和用法。

---

## 目录

- [基础设置](#基础设置)
- [手牌范围设置](#手牌范围设置)
- [下注尺寸设置](#下注尺寸设置)
- [求解控制参数](#求解控制参数)
- [执行与输出](#执行与输出)

---

## 基础设置

### `set_pot`

**语法**: `set_pot <数值>`

**示例**: `set_pot 5`

**说明**: 设置当前底池大小，单位为大盲注（BB）。

---

### `set_effective_stack`

**语法**: `set_effective_stack <数值>`

**示例**: `set_effective_stack 100`

**说明**: 设置有效筹码深度，单位为大盲注（BB）。有效筹码是指两位玩家中筹码较少者的筹码量。

---

### `set_board`

**语法**: `set_board <牌1>,<牌2>,<牌3>[,<牌4>[,<牌5>]]`

**示例**: `set_board Ac,Ad,Ah`

**说明**: 设置公共牌面。

**牌面格式**:
- 牌面值: `A`, `K`, `Q`, `J`, `T`, `9`, `8`, `7`, `6`, `5`, `4`, `3`, `2`
- 花色: 
  - `c` = 梅花 (Clubs ♣)
  - `d` = 方块 (Diamonds ♦)
  - `h` = 红桃 (Hearts ♥)
  - `s` = 黑桃 (Spades ♠)

**示例解析**: `Ac,Ad,Ah` 表示翻牌面为梅花A、方块A、红桃A。

---

## 手牌范围设置

### `set_range_oop`

**语法**: `set_range_oop <手牌范围表达式>`

**说明**: 设置 OOP（Out of Position，位置不利方）的起手牌范围。

---

### `set_range_ip`

**语法**: `set_range_ip <手牌范围表达式>`

**说明**: 设置 IP（In Position，位置有利方）的起手牌范围。

---

### 手牌范围表达式格式

手牌之间用逗号分隔，支持以下格式：

| 格式 | 说明 | 示例 |
|------|------|------|
| `XYs` | 同花手牌 | `AKs`, `QJs` |
| `XYo` | 非同花手牌 | `AKo`, `QJo` |
| `XX` | 口袋对 | `AA`, `KK`, `22` |
| `手牌:频率` | 指定进入范围的频率 (0-1) | `A5s:0.75` 表示75%频率 |

**完整示例**:
```
AA,AKs,AQs:0.5,KK,QQ:0.75
```
表示: AA和AKs 100%进入范围，AQs 50%进入，KK 100%进入，QQ 75%进入。

---

## 下注尺寸设置

### `set_bet_sizes`

**语法**: `set_bet_sizes <位置>,<街道>,<动作类型>,<尺寸列表>`

**说明**: 设置指定位置、街道的可选下注/加注尺寸。

#### 位置参数

| 值 | 含义 |
|----|------|
| `oop` | 位置不利方 (Out of Position) |
| `ip` | 位置有利方 (In Position) |

#### 街道参数

| 值 | 含义 |
|----|------|
| `flop` | 翻牌圈 |
| `turn` | 转牌圈 |
| `river` | 河牌圈 |

#### 动作类型参数

| 值 | 含义 |
|----|------|
| `bet` | 下注（对方 check 后的下注） |
| `raise` | 加注（对方下注后的加注） |
| `donk` | 领先下注（OOP 在新一轮首先下注，无视上一轮的攻击权） |
| `allin` | 允许全下选项（无需指定尺寸） |

#### 尺寸数值

尺寸数值表示 **底池的百分比**。例如：
- `33` = 下注底池的 33%
- `75` = 下注底池的 75%
- `150` = 下注底池的 150%（超池下注）

**示例**:
```
set_bet_sizes oop,flop,bet,33
set_bet_sizes ip,turn,bet,50,75,150
set_bet_sizes ip,river,allin
```

---

## 求解控制参数

### `set_allin_threshold`

**语法**: `set_allin_threshold <数值>`

**示例**: `set_allin_threshold 0.5`

**说明**: 设置全下阈值。当某次下注/加注的金额达到剩余有效筹码的该比例时，自动视为全下。

**示例解析**: `0.5` 表示当下注金额 ≥ 剩余筹码的 50% 时，自动转换为全下。

---

### `set_raise_limit`

**语法**: `set_raise_limit <数值>`

**示例**: `set_raise_limit 2`

**说明**: 设置每条街的最大加注次数限制。超过此限制后不再允许继续加注。

---

### `build_tree`

**语法**: `build_tree`

**说明**: 根据之前设置的所有参数（底池、筹码、牌面、范围、下注尺寸等）构建博弈树。

> ⚠️ **注意**: 此命令必须在设置完所有游戏参数后、开始求解前执行。

---

### `set_thread_num`

**语法**: `set_thread_num <数值>`

**示例**: `set_thread_num 8`

**说明**: 设置求解时使用的 CPU 线程数。建议设置为 CPU 核心数或略少。

---

### `set_accuracy`

**语法**: `set_accuracy <数值>`

**示例**: `set_accuracy 1`

**说明**: 设置求解精度目标（以底池百分比表示的可利用度）。数值越小精度越高，但计算时间越长。

| 精度值 | 含义 |
|--------|------|
| `0.5` | 高精度（可利用度 < 0.5% 底池） |
| `1` | 标准精度 |
| `2` | 快速求解（精度较低） |

---

### `set_max_iteration`

**语法**: `set_max_iteration <数值>`

**示例**: `set_max_iteration 300`

**说明**: 设置 CFR 算法的最大迭代次数。即使未达到精度目标，也会在达到此迭代次数后停止。

---

### `set_print_interval`

**语法**: `set_print_interval <数值>`

**示例**: `set_print_interval 10`

**说明**: 设置求解过程中打印进度信息的间隔（每隔多少次迭代输出一次）。

---

### `set_use_isomorphism`

**语法**: `set_use_isomorphism <0|1>`

**示例**: `set_use_isomorphism 1`

**说明**: 是否启用同构优化。

| 值 | 含义 |
|----|------|
| `1` | 启用（利用花色对称性减少计算量，推荐） |
| `0` | 禁用 |

---

### `set_enable_equity`

**语法**: `set_enable_equity <0|1>`

**示例**: `set_enable_equity 1`

**说明**: 是否启用 Equity（胜率期望）的计算和导出。

| 值 | 含义 |
|----|------|
| `1` | 启用（计算并导出 equity，会增加少量计算开销和输出文件大小） |
| `0` | 禁用（默认，只计算 EV） |

**Equity 定义**:
```
equity = win_prob + tie_prob / 2
```
- 赢：算 1.0
- 平局：算 0.5
- 输：算 0.0

**与 EV 的区别**:

| 指标 | 含义 | 单位 |
|-----|------|-----|
| **EV** | 期望收益 | 筹码数 |
| **Equity** | 胜率期望 | 概率 (0-1) |

> 💡 **提示**: 默认不启用。只有在需要分析胜率相关信息时才需要开启。

---

## 执行与输出

### `start_solve`

**语法**: `start_solve`

**说明**: 开始执行 CFR 求解算法。求解器将根据之前构建的博弈树进行迭代计算，直到达到精度目标或最大迭代次数。

---

### `set_dump_rounds`

**语法**: `set_dump_rounds <数值>`

**示例**: `set_dump_rounds 1`

**说明**: 设置导出策略时包含的发牌轮次深度。

| 值 | 导出内容 |
|----|----------|
| `1` | 仅导出 Flop（翻牌圈）策略 |
| `2` | 导出 Flop + Turn（翻牌圈 + 转牌圈）策略 |
| `3` | 导出 Flop + Turn + River（完整三条街）策略 |

**源码参考** (`PCfrSolver.cpp`):
```cpp
void PCfrSolver::reConvertJson(..., int depth, int max_depth, ...) {
    if(depth >= max_depth) return;  // 达到最大深度时停止导出
    ...
}
```

每经过一个 chance node（发牌节点，即发 turn 或 river 牌），`depth` 增加 1。`dump_rounds` 作为 `max_depth` 参数控制递归导出的深度。

> 💡 **提示**: 较大的 `dump_rounds` 值会显著增加输出文件大小。如果只需要分析翻牌圈策略，设置为 `1` 即可。

---

### `dump_result`

**语法**: `dump_result <文件名>`

**示例**: `dump_result my_strategy.json`

**说明**: 将求解结果导出到指定的 JSON 文件。文件将保存到当前工作目录或 `results/` 目录下。

---

## 完整配置示例

```
# 基础设置
set_pot 5
set_effective_stack 100
set_board Ac,Ad,Ah

# 手牌范围
set_range_oop AQs,AJs,ATs,KQs,KJs,QQ:0.25,JJ:0.75,...
set_range_ip AA,AKs,AQs,KK,QQ,JJ,...

# 下注尺寸 - OOP
set_bet_sizes oop,flop,bet,33
set_bet_sizes oop,flop,raise,75
set_bet_sizes oop,turn,bet,25,50,75,150
set_bet_sizes oop,turn,raise,75,150
set_bet_sizes oop,turn,donk,33
set_bet_sizes oop,river,bet,25,50,75,150
set_bet_sizes oop,river,raise,75,150
set_bet_sizes oop,river,donk,33
set_bet_sizes oop,river,allin

# 下注尺寸 - IP
set_bet_sizes ip,flop,bet,25,50,75
set_bet_sizes ip,flop,raise,75
set_bet_sizes ip,turn,bet,50,75,150
set_bet_sizes ip,turn,raise,75,150
set_bet_sizes ip,river,bet,33,75,110,150
set_bet_sizes ip,river,raise,75,150
set_bet_sizes ip,river,allin

# 求解控制
set_allin_threshold 0.5
set_raise_limit 2
build_tree
set_thread_num 8
set_accuracy 1
set_max_iteration 300
set_print_interval 10
set_use_isomorphism 1
# set_enable_equity 1  # 可选：启用 equity 计算

# 执行求解
start_solve

# 导出结果
set_dump_rounds 1
dump_result my_strategy.json
```

---

## 参考资料

- 项目源码: `TexasSolver-console/src/tools/CommandLineTool.cpp`
- 策略导出逻辑: `TexasSolver-console/src/solver/PCfrSolver.cpp`
