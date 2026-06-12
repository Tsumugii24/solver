# Pipeline 状态文件与外部监控

本文档说明 `run_pipeline.py` 如何将运行状态写入 JSON 文件，供外部自动化程序（例如通过 SSH 访问服务器的监控脚本）读取当前 `repo_id`、场景、进度等信息，无需人工输入。

---

## 目录

- [概述](#概述)
- [状态文件位置](#状态文件位置)
- [更新机制与频率](#更新机制与频率)
- [写入时机](#写入时机)
- [字段说明](#字段说明)
- [status 取值](#status-取值)
- [配置方式](#配置方式)
- [SSH 监控示例](#ssh-监控示例)
- [监控程序集成建议](#监控程序集成建议)
- [场景与默认参数](#场景与默认参数)
- [常见问题](#常见问题)

---

## 概述

`run_pipeline.py` 在运行期间会持续更新一个 JSON 状态文件。外部程序只需读取该文件，即可获知：

- 当前 Hugging Face `repo_id`（目标 dataset）
- 推断出的 `scenario`（如 `sia-sod`、`3ia-3od`）
- 使用的 range 文件路径
- 当前 batch 进度、进程 PID、主机名
- 流水线是否仍在运行、是否已结束

典型用途：监控程序定时 SSH 到求解服务器，读取状态文件后自动同步 Hugging Face dataset 进度，或向下游系统汇报当前任务。

---

## 状态文件位置

默认使用**固定绝对路径**，与 solver 在哪个目录启动（`pwd`）无关，方便监控程序在所有服务器上用同一路径读取。

| 优先级 | 来源 | 路径 |
|--------|------|------|
| 1 | 命令行 `--status-file` | 用户指定绝对/相对路径 |
| 2 | 环境变量 `PIPELINE_STATUS_FILE` | 用户指定路径 |
| 3 | 默认 | `~/run/solver_running_status.json`（展开后如 `/home/user/run/solver_running_status.json`） |

启动时控制台会打印实际绝对路径：

```
[Status] 外部可读状态文件: /home/user/run/solver_running_status.json
```

首次写入时会自动创建 `~/run/` 目录。写入采用「先写 `.tmp` 再原子替换」方式，避免监控程序读到半截 JSON。

---

## 更新机制与频率

**会更新。** 只要 pipeline 在跑且未加 `--no-status-file`，状态文件就会随任务推进被反复覆盖写入。

**不是定时轮询**，而是**事件驱动**：仅在 pipeline 主进程发生特定事件时更新，没有后台心跳线程。

| 事件 | 是否写文件 | 典型间隔 |
|------|------------|----------|
| 启动完成、进入求解 | 是 | 一次 |
| 每个 batch 开始前 | 是 | 每个 batch 一次（默认 `--batch-size 5` 即每 5 个牌面一批） |
| 单个 batch 求解过程中 | **否** | 可能持续数分钟～数小时（取决于牌面难度） |
| 触发上传 / cleanup | 是 | 每积累够 `batch_size` 个导出文件一次 |
| 正常结束 / 异常退出 | 是 | 一次 |

因此：

- **batch 之间**：`updated_at` 会刷新，`current_batch` 会递增。
- **单个 batch 求解期间**：文件内容可能长时间不变，这不代表进程死掉，而是 `auto_run_solver.py` 子进程仍在算。
- 监控程序应结合 `status`、`pid`（`ps` 校验）和 `updated_at` 判断；不要假设文件会每秒更新。

推荐监控轮询间隔：**30～60 秒**（比 batch 内求解频率低即可，无需与文件更新同步）。

---

## 写入时机

| 阶段 | 行为 |
|------|------|
| 流水线启动、参数校验完成后 | 写入初始状态，`status=running` |
| 每个 batch 开始前 | 更新 `current_batch`、`batch_expr` 等 |
| solver batch 失败 | 更新 `last_solver_success=false` |
| 触发上传前 | 更新 `phase=uploading` |
| 上传成功/失败 | 更新 `last_upload_success`、`phase=solving` |
| 最终 cleanup 上传 | 更新 `phase=cleanup` |
| 正常结束 | `status=completed` 或 `completed_with_upload_failures` |
| 异常退出 | `status=failed`，附带 `error` |
| 进程意外退出（未显式 finalize） | `atexit` 钩子写入 `status=exited` |

使用 `--dry-run` 时**不会**写入状态文件（预览后直接退出）。

使用 `--no-status-file` 可完全关闭状态写入。

---

## 字段说明

### 始终存在的字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `started_at` | string | 启动时间（UTC，ISO 8601，如 `2026-06-13T10:00:00Z`） |
| `updated_at` | string | 最近一次更新时间 |
| `pid` | int | 当前 `run_pipeline.py` 进程 ID |
| `host` | string | 主机名（`socket.gethostname()`） |
| `status_file` | string | 状态文件绝对路径 |
| `status` | string | 当前状态，见 [status 取值](#status-取值) |
| `command` | string | 启动时的完整命令行 |

### 任务相关字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `repo_id` | string \| null | Hugging Face dataset repo_id，如 `Tsumugii/sia-45-sod-40` |
| `dataset_name` | string \| null | `repo_id` 最后一段，如 `sia-45-sod-40` |
| `scenario` | string \| null | 求解场景，如 `sia-sod`、`3ia-3od` |
| `range_file` | string \| null | range 文件相对项目根的路径，如 `ranges/sia-sod/sia-45-sod-40.txt` |
| `upload_enabled` | bool | 是否启用上传（`--no-upload` 时为 false） |
| `no_upload` | bool | 是否传入 `--no-upload` |
| `convert_only` | bool | 是否为 `--convert-only` 模式 |
| `cards_file` | string | 牌面列表文件名，默认 `cards.txt` |
| `export_format` | string | solver 导出格式（`json` / `parquet` 等） |
| `upload_format` | string | 上传到 HF 的格式 |
| `batch_size` | int | 触发上传的 batch 阈值 |
| `total_tasks` | int | 待求解牌面总数 |
| `total_batches` | int | solver batch 总数 |
| `current_batch` | int | 当前 batch 序号（1-based，运行中为 ≥1） |

### 运行中可能出现的字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `batch_expr` | string | 当前 batch 的序号表达式，如 `11-15` |
| `batch_size_current` | int | 当前 batch 包含的牌面数 |
| `phase` | string | 当前阶段：`solving` / `uploading` / `cleanup` |
| `pending_export_count` | int | `results/` 中待处理导出文件数 |
| `last_solver_success` | bool | 最近一次 solver batch 是否全部成功 |
| `last_upload_success` | bool | 最近一次上传是否成功 |

### 结束时的额外字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `finished_at` | string | 结束时间（UTC） |
| `upload_failures` | int | 上传失败次数 |
| `remaining_export_files` | int | 结束时仍留在 `results/` 的文件数 |
| `error` | string | 异常退出时的错误信息 |
| `message` | string | 附加说明（如 `no artifacts to process`） |
| `mode` | string | 特殊结束模式（如 `convert_only`） |

---

## status 取值

| 值 | 含义 |
|----|------|
| `running` | 流水线正在运行 |
| `completed` | 正常结束 |
| `completed_with_upload_failures` | 求解完成，但有过上传失败 |
| `failed` | 因未捕获异常退出 |
| `exited` | 进程退出但未走到正常 finalize（如被 kill 前未来得及更新） |

**判断是否在跑：** 同时检查 `status == "running"` 且 `pid` 对应进程仍存在。

---

## 配置方式

### 默认（推荐）

```bash
python run_pipeline.py 1-20 --repo-id Tsumugii/sia-45-sod-40
# 状态写入 ~/run/solver_running_status.json
```

### 自定义路径（环境变量）

```bash
export PIPELINE_STATUS_FILE=$HOME/run/solver_running_status.json
python run_pipeline.py 1-20 --repo-id Tsumugii/sia-45-sod-40
```

### 自定义路径（命令行）

```bash
python run_pipeline.py 1-20 \
  --repo-id Tsumugii/sia-45-sod-40 \
  --status-file $HOME/run/solver_running_status.json
```

### 关闭状态写入

```bash
python run_pipeline.py 1-20 --no-upload --no-status-file
```

---

## SSH 监控示例

状态文件使用固定默认路径，与 solver 仓库所在目录无关。

### 查看完整状态

```bash
ssh user@server 'cat ~/run/solver_running_status.json'
```

### 只读取 repo_id

```bash
ssh user@server 'jq -r ".repo_id" ~/run/solver_running_status.json'
```

### 仅在运行中时读取 repo_id

```bash
ssh user@server 'jq -r "select(.status==\"running\") | .repo_id" ~/run/solver_running_status.json'
```

### 确认进程是否存活

```bash
ssh user@server 'bash -s' <<'EOF'
STATUS_FILE=~/run/solver_running_status.json
PID=$(jq -r '.pid' "$STATUS_FILE")
STATUS=$(jq -r '.status' "$STATUS_FILE")
REPO=$(jq -r '.repo_id' "$STATUS_FILE")

if [ "$STATUS" = "running" ] && ps -p "$PID" > /dev/null 2>&1; then
  echo "RUNNING repo_id=$REPO pid=$PID"
else
  echo "NOT_RUNNING status=$STATUS last_repo_id=$REPO"
fi
EOF
```

### Python 读取（无 jq 时）

```python
import json
from pathlib import Path

status_path = Path.home() / "run" / "solver_running_status.json"
data = json.loads(status_path.read_text(encoding="utf-8"))

if data.get("status") == "running":
    repo_id = data["repo_id"]
    scenario = data["scenario"]
    batch = data.get("current_batch")
    total = data.get("total_batches")
    print(f"sync target: {repo_id} ({scenario}) batch {batch}/{total}")
```

---

## 监控程序集成建议

1. **固定状态文件路径**  
   在服务器上用 `PIPELINE_STATUS_FILE` 或 `--status-file` 指向统一位置，监控侧不必猜测路径。

2. **不要只信 `status`**  
   同时用 `pid` + `ps` 校验，避免进程已死但文件仍显示 `running`（例如被 `SIGKILL`）。

3. **`repo_id` 可能为 null**  
   使用 `--no-upload` 且未传 `--repo-id`、仅指定 `--scenario` + `--range-file` 时，没有 HF 目标。此时可用 `scenario` + `range_file` 识别任务。

4. **单文件覆盖**  
   同一机器上后启动的 pipeline 会覆盖前一个的状态。若需并行多任务，应为每个实例指定不同的 `--status-file`。

5. **轮询间隔**  
   建议 30s～60s 轮询。文件仅在 batch 切换、上传等事件时更新，单个 batch 求解期间可能长时间不变；结合 `updated_at` 与 `ps` 判断即可。

6. **与 HF dataset 同步**  
   监控程序拿到 `repo_id` 后，可调用自有逻辑对比 HF 上已有牌面与 `total_tasks - current_batch` 估算的剩余量。

---

## 场景与默认参数

状态文件中的 `scenario` 由 range 文件路径或文件名自动推断，`run_pipeline.py` 会将其传给 `auto_run_solver.py`，并决定默认 `pot` / `effective_stack`（除非手动传 `--pot` / `--stack`）。

| scenario | 默认 pot | 默认 effective_stack | 配置模板 |
|----------|----------|----------------------|----------|
| `sia-sod` | 5 | 98 | SIA_SOD_CONFIG |
| `sia-sod-open2` | 4 | 98 | SIA_SOD_CONFIG |
| `sia-sod-open2.5` | 5 | 98 | SIA_SOD_CONFIG |
| `sia-sod-open3` | 6 | 97 | SIA_SOD_CONFIG |
| `soa-sid` | 5 | 98 | SOA_SID_CONFIG |
| `3ia-3od` | 16 | 92 | TOA_TID_CONFIG |

### scenario 推断规则

**子目录优先：**

```
ranges/sia-sod/...           → sia-sod
ranges/sia-sod-open2/...     → sia-sod-open2
ranges/sia-sod-open2.5/...   → sia-sod-open2.5
ranges/sia-sod-open3/...     → sia-sod-open3
ranges/soa-sid/...           → soa-sid
ranges/3ia-3od/...          → 3ia-3od
```

**文件名后备（仍在 `ranges/sia-sod/` 下时）：**

| 文件名包含 | scenario |
|------------|----------|
| `open2.5` | `sia-sod-open2.5` |
| `open3` | `sia-sod-open3` |
| `open2` | `sia-sod-open2` |
| `-sod-` / `sia-...sod...` | `sia-sod` |

匹配顺序：`open2.5` → `open3` → `open2`，避免 `open2.5` 被误判为 `open2`。

### 切换任务时的 cards 配置

每次求解前，`auto_run_solver.py` 会以覆盖写方式重新生成 `cards/<牌面>.txt`。因此先跑 `3ia-3od` 再跑 `sia-sod` 时，**本次 batch 涉及的牌面**会自动更新为新的 pot/stack 和 range；未再次求解的牌面 txt 仍保留旧值，但不会被使用。

---

## 常见问题

### Q: 监控读到的 `repo_id` 是 `null`？

A: 当前任务可能使用了 `--no-upload` 且只指定了 `--scenario` + `--range-file`。改用 `--repo-id` 或设置 `HF_REPO_ID` 即可写入 `repo_id`。

### Q: `status` 一直是 `running`，但 pipeline 明明已经结束？

A: 进程可能被强制杀死（`SIGKILL`），`atexit` 未执行。用 `ps -p <pid>` 确认；若进程不存在，应视为已停止，以 `updated_at` 停滞作为辅助判断。

### Q: 如何对接多个 solver 服务器？

A: 每台机器配置独立的 `PIPELINE_STATUS_FILE` 路径，监控程序按主机拉取对应文件即可。

### Q: 状态文件里能看到完整命令行吗？

A: 可以。`command` 字段保存了启动时的 `sys.argv` 拼接结果，便于审计和复现。

---

## 完整示例

运行：

```bash
python run_pipeline.py 1-10 --repo-id Tsumugii/3ia-16.5-3od-13
```

运行中 `solver_running_status.json` 可能类似：

```json
{
  "started_at": "2026-06-13T10:00:00Z",
  "pid": 12345,
  "host": "solver-01",
  "status_file": "/home/user/run/solver_running_status.json",
  "status": "running",
  "repo_id": "Tsumugii/3ia-16.5-3od-13",
  "dataset_name": "3ia-16.5-3od-13",
  "scenario": "3ia-3od",
  "range_file": "ranges/3ia-3od/3ia-16.5-3od-13.txt",
  "upload_enabled": true,
  "no_upload": false,
  "convert_only": false,
  "total_tasks": 10,
  "total_batches": 2,
  "current_batch": 1,
  "batch_expr": "1-5",
  "batch_size_current": 5,
  "export_format": "parquet",
  "upload_format": "parquet",
  "cards_file": "cards.txt",
  "batch_size": 5,
  "command": "python run_pipeline.py 1-10 --repo-id Tsumugii/3ia-16.5-3od-13",
  "updated_at": "2026-06-13T10:05:00Z"
}
```

结束后：

```json
{
  "status": "completed",
  "finished_at": "2026-06-13T10:30:00Z",
  "upload_failures": 0,
  "updated_at": "2026-06-13T10:30:00Z"
}
```

（其余字段保留自运行期间写入的值。）

---

## 相关文件

| 文件 | 说明 |
|------|------|
| `run_pipeline.py` | 流水线主程序，内含 `PipelineStatusTracker` |
| `auto_run_solver.py` | 被 pipeline 调用的批量求解脚本 |
| `~/run/solver_running_status.json` | 默认状态输出（运行时生成，勿提交到 git） |
| `docs/PARAMETERS_DOC.md` | solver 配置参数说明 |
