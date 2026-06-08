# Docker 一键部署

把 **系统依赖 + Apache Arrow + 编译好的 `console_solver` + Python 流水线依赖** 全部打进镜像。  
在新服务器上只需 `docker pull` 一次，即可直接跑 `run_pipeline.py`，无需再装 Miniconda、apt 包或现场编译。

> **安全提示**：不要把 Hugging Face Token 写进镜像或提交到 Git。运行时通过环境变量传入（见下文）。

---

## 镜像里已包含

| 组件 | 说明 |
|------|------|
| `install/console_solver` | 构建阶段用 `compile.sh --skip-deps` 预编译 |
| Apache Arrow / Parquet | C++ 库（solver 原生 parquet 导出） |
| Python 包 | `pyarrow`、`huggingface_hub`、`tqdm`、`openpyxl` |
| 项目代码 | `run_pipeline.py`、`auto_run_solver.py`、`ranges/`、`cards/` 等 |

**不包含**（通过私有仓 [solver-secrets](https://github.com/Tsumugii24/solver-secrets) 挂载）：

- Clash 配置、`HF_TOKEN` → `~/solver-secrets`（`gh token` clone）
- `results/` 持久化（挂载 volume）

---

## 推荐：solver-secrets + 一键跑 pipeline

```bash
# 1. 在 solver 目录配置 GH_TOKEN（仅 bootstrap 用，见 .env.docker.example）
export GH_TOKEN=ghp_xxxx
export SECRETS_DIR=~/solver-secrets
./scripts/bootstrap-secrets.sh    # clone 私有仓，首次会提示编辑 ~/solver-secrets/.env

# 2. 编辑 secrets 后再次 bootstrap（下载 Clash config）
nano ~/solver-secrets/.env
./scripts/bootstrap-secrets.sh

# 3. 构建或 pull 镜像
docker compose build
# 或: export SOLVER_IMAGE=ghcr.io/tsumugii24/solver-pipeline:latest

# 4. 只写牌面范围；HF_REPO_ID 已在 secrets/.env
./scripts/run-pipeline.sh 1-20
```

---

## 1. 构建镜像（只需做一次，建议在网速好的机器上）

```bash
cd solver
docker build -t solver-pipeline:latest .
```

或使用 compose：

```bash
docker compose build
```

首次构建会下载 apt 包、Arrow 源并编译 C++，耗时较长（约 15–40 分钟，视 CPU 与网络而定）。  
之后推送到 Registry，其他机器只需 pull。

### 推送到 GitHub Container Registry

```bash
# 登录（一次性）
echo "$GITHUB_TOKEN" | docker login ghcr.io -u YOUR_GITHUB_USER --password-stdin

# 打标签并推送
docker tag solver-pipeline:latest ghcr.io/YOUR_GITHUB_USER/solver-pipeline:latest
docker push ghcr.io/YOUR_GITHUB_USER/solver-pipeline:latest
```

---

## 2. 新服务器上一键运行

```bash
# 安装 Docker（Ubuntu 示例）
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2
sudo usermod -aG docker "$USER"
# 重新登录 shell 后：

docker pull ghcr.io/YOUR_GITHUB_USER/solver-pipeline:latest
docker tag ghcr.io/YOUR_GITHUB_USER/solver-pipeline:latest solver-pipeline:latest
```

创建 `.env`（不要提交到 Git）：

```bash
cat > .env <<'EOF'
HF_TOKEN=hf_xxxxxxxx
HF_REPO_ID=Tsumugii/sia-12-sod-30
EOF
```

克隆仓库（若只用镜像内代码可跳过，但通常需要挂载 `results/`）：

```bash
git clone https://github.com/Tsumugii24/solver.git
cd solver
```

运行 pipeline：

```bash
export HF_TOKEN=hf_xxxxxxxx

docker run --rm -it \
  -e HF_TOKEN \
  -e HUGGINGFACE_HUB_TOKEN="$HF_TOKEN" \
  -v "$(pwd)/results:/app/results" \
  solver-pipeline:latest \
  python3 run_pipeline.py 1-20 --repo-id Tsumugii/sia-12-sod-30
```

或使用 docker compose：

```bash
# docker-compose.yml 已配置 results 挂载与 HF_TOKEN
HF_TOKEN=hf_xxx HF_REPO_ID=Tsumugii/sia-12-sod-30 \
  docker compose run --rm pipeline \
  python3 run_pipeline.py 1-20 --repo-id "$HF_REPO_ID"
```

### 常用参数示例

```bash
# 只求解，不上传
python3 run_pipeline.py 1-20 --no-upload --repo-id user/dataset

# 仅转换并上传已有 results
python3 run_pipeline.py --convert-only --repo-id user/dataset

# 指定牌面
python3 run_pipeline.py Jc7c5c,AcKc3d --repo-id user/dataset
```

### 需要代理时

```bash
docker run --rm -it \
  -e HF_TOKEN \
  -e http_proxy=http://host.docker.internal:7890 \
  -e https_proxy=http://host.docker.internal:7890 \
  -v "$(pwd)/results:/app/results" \
  solver-pipeline:latest \
  python3 run_pipeline.py 1-20 --repo-id user/dataset
```

Linux 上若代理在宿主机 `127.0.0.1:7890`，改用 `--network host` 或宿主机局域网 IP。

---

## 3. 与手动 setup 的对应关系

| 原 setup.md 步骤 | Docker 方案 |
|------------------|-------------|
| 安装 Miniconda | 不需要，镜像内已有 Python3 + pip 包 |
| `apt install git tmux` | 不需要（代码已在镜像内；持久化用 volume） |
| Clash 代理 | 可选，`HTTP_PROXY` 环境变量 |
| `pip install huggingface_hub` | 已预装 |
| `git clone` + `python run_pipeline.py` | `docker run ... python3 run_pipeline.py ...` |
| 首次自动 `compile.sh` + Arrow apt | 已在镜像构建阶段完成 |

---

## 4. 故障排查

**验证 solver 是否存在：**

```bash
docker run --rm solver-pipeline:latest ls -la install/console_solver
```

**验证 Python 依赖：**

```bash
docker run --rm solver-pipeline:latest python3 -c "import pyarrow; import huggingface_hub; print('ok')"
```

**重新编译 solver（开发用）：** 修改 C++ 代码后重新 `docker build`，或在容器内挂载源码并手动编译（不推荐生产环境）。

---

## 5. 文件说明

| 文件 | 作用 |
|------|------|
| `Dockerfile` | 多阶段构建：编译 + 运行时 |
| `docker/install-build-deps.sh` | 构建阶段 apt / Arrow 依赖 |
| `docker/install-runtime-deps.sh` | 运行时共享库（不含编译器） |
| `docker/entrypoint.sh` | 入口，检查 `console_solver` 存在 |
| `docker-compose.yml` | 本地/服务器快捷运行 |
| `requirements.txt` | Python 流水线依赖 |
