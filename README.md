# HiClaw Py

HiClaw Py 是一个面向长期运行、可扩展和可观测的多通道 AI Agent 工程。

它当前已经具备：

- 多通道接入：Telegram、Feishu、PowerShell TUI
- 双 Provider 路由：Claude / OpenAI
- 统一能力层：tools / workflows / skills
- 文件读写、命令执行、联网搜索、定时任务、分层记忆
- Dashboard 可视化：classic / v2 / core
- 多 Agent 集群 runtime foundation：planner / executor / reviewer 基础模型与监控投影

当前它不是一个“最终形态的自治 Agent Swarm”，但已经是一个可直接运行、可继续演进到多 Agent 协作系统的工程底座。

## 当前定位

- 可长期运行的个人 Agent 平台
- 多 Provider、多工具、多工作流的工程样板
- 正在从单 Agent 执行框架演进到多 Agent cluster runtime 的系统

## 主要能力

### 交互入口

- Telegram Bot
- Feishu 长连接机器人
- PowerShell TUI

### Provider 能力

- Claude：更适合复杂工具调用、文件修改、长链执行、workflow 场景
- OpenAI：更适合通用对话、图像理解、图像生成与编辑

### 能力系统

- 统一 `ToolSpec` registry
- registry-backed tool discovery
- workflow CRUD
- 自然语言生成 workflow
- skill 管理与热加载
- 联网搜索工具 `web_search`：可选 Tavily 增强，未配置时回退默认轻量搜索

### 运行时能力

- decision layer：意图理解、能力候选排序、策略路由
- confirmation layer：高风险工具执行确认
- scheduler：定时任务 / 提醒
- memory：长期记忆、工作记忆、会话偏好
- monitor dashboard：实时 activity snapshot

### 多 Agent 集群现状

已经落地：

- `planner / executor / reviewer` 角色模型
- cluster runtime store
- `runs / tasks / messages / agents / events` 基础数据结构
- dashboard cluster projection

仍在演进：

- planner 的真实拆解链
- reviewer 的真实审查链
- 多 executor 并行
- 完整 task DAG / dependency scheduler

## 工程结构

```text
src/hiclaw/
  app.py                    主启动入口
  config.py                 环境配置

  agents/                   Provider 执行层
    runtime.py              单轮执行总入口
    router.py               Provider 路由
    claude.py               Claude 执行
    openai.py               OpenAI 执行

  decision/                 意图理解与策略决策
  capabilities/             tools / workflows / registry
  cluster/                  多 Agent 集群基础层
  memory/                   分层记忆系统
  tasks/                    定时任务与调度
  channels/                 Telegram / Feishu / TUI
  monitor/                  dashboard server 与前端资源
  core/                     公共类型、delivery、activity 等
  skills/                   skill 管理
  media/                    图片 / 语音相关处理

pixel-office-core/          像素办公室 core 渲染与素材
scripts/                    Linux 启停脚本
```

## Dashboard

项目当前有 3 个 dashboard 入口：

- classic：`/`
- v2：`/v2`
- core：`/core`

其中：

- `classic` 是早期基础看板
- `v2` 是当前 cluster 可视化主界面
- `core` 使用 `pixel-office-core` 独立渲染像素办公室

如果通过主程序启动，dashboard 会随主进程一起启动。

## Quickstart

### 1. Install

Linux / macOS / WSL2：

```bash
curl -fsSL https://raw.githubusercontent.com/lianjiawei/hiclaw-py/master/scripts/install.sh | bash
```

Windows PowerShell：

```powershell
irm https://raw.githubusercontent.com/lianjiawei/hiclaw-py/master/scripts/install.ps1 | iex
```

### 2. Configure

```bash
hiclaw setup
```

### 3. Check

```bash
hiclaw doctor
```

### 4. Run

Linux / macOS / WSL2 后台运行，适合服务器长期在线：

```bash
hiclaw start
hiclaw status
hiclaw logs -f
hiclaw stop
```

前台运行，适合本地调试或 Windows 原生环境：

```bash
hiclaw run
```

本地 TUI 调试：

```bash
hiclaw-tui
```

### 5. Uninstall

Linux / macOS / WSL2：

```bash
curl -fsSL https://raw.githubusercontent.com/lianjiawei/hiclaw-py/master/scripts/uninstall.sh | bash
```

Windows PowerShell：

```powershell
irm https://raw.githubusercontent.com/lianjiawei/hiclaw-py/master/scripts/uninstall.ps1 | iex
```

卸载脚本默认会删除：

- HiClaw 安装目录
- 独立 Python 虚拟环境
- `hiclaw` / `hiclaw-tui` / `hiclaw-dashboard` / `hiclaw-feishu` 命令包装器
- Windows 用户 PATH 中的 HiClaw bin 目录

如果你想保留安装目录里的 `.env`、`data/`、`workspace/` 等本地数据：

```bash
HICLAW_KEEP_DATA=1 curl -fsSL https://raw.githubusercontent.com/lianjiawei/hiclaw-py/master/scripts/uninstall.sh | bash
```

PowerShell：

```powershell
$env:HICLAW_KEEP_DATA="1"; irm https://raw.githubusercontent.com/lianjiawei/hiclaw-py/master/scripts/uninstall.ps1 | iex
```

## Website

项目主页静态站点位于 `site/`：

```bash
cd site
python -m http.server 8080
```

访问：

```text
http://127.0.0.1:8080
```

这个站点不依赖构建工具，可以直接部署到 GitHub Pages、Nginx、Cloudflare Pages 或任何静态托管服务。

## 手动安装

### 环境要求

- Python `>= 3.12`
- Git
- 推荐使用 `venv`
- 如果要使用 `/core` dashboard，Linux 服务器推荐安装 Node.js 和 npm

一键安装脚本默认会：

- 克隆项目到 `~/.hiclaw/hiclaw-py`（Windows 为 `%LOCALAPPDATA%\HiClaw\hiclaw-py`）
- 创建独立 Python 虚拟环境
- 安装 `hiclaw`、`hiclaw-tui`、`hiclaw-dashboard`、`hiclaw-feishu` 命令
- 如果检测到 npm，自动构建 `pixel-office-core`

可通过环境变量自定义安装：

```bash
HICLAW_INSTALL_DIR=/opt/hiclaw HICLAW_BRANCH=master curl -fsSL https://raw.githubusercontent.com/lianjiawei/hiclaw-py/master/scripts/install.sh | bash
```

PowerShell：

```powershell
$env:HICLAW_INSTALL_DIR="$env:USERPROFILE\Apps\HiClaw"; irm https://raw.githubusercontent.com/lianjiawei/hiclaw-py/master/scripts/install.ps1 | iex
```

### 1. 克隆仓库

```bash
git clone git@github.com:lianjiawei/hiclaw-py.git
cd hiclaw-py
```

### 2. 创建 Python 环境

Linux / macOS 推荐直接使用系统自带 `python3` + `venv`：

```bash
python3 -m venv .venv
source .venv/bin/activate
```

说明：

- 创建虚拟环境前，Linux 上优先使用 `python3`
- 激活 `.venv` 后，后续命令统一使用 `python`

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Conda 仅作为本地开发可选项：

```bash
conda create -n hiclaw python=3.12 -y
conda activate hiclaw
```

### 3. 安装依赖

```bash
python -m pip install -U pip
python -m pip install -e .
```

如果需要本地语音识别：

```bash
python -m pip install -e ".[asr]"
```

### 4. 可选：为 `/core` dashboard 准备前端依赖

如果你会通过 Linux `scripts/start.sh` 启动，并且机器已经安装了 npm，脚本会自动在 `pixel-office-core/` 下执行：

- `npm ci` 或 `npm install`
- `npm run build`

如果你想手动准备：

```bash
cd pixel-office-core
npm ci
npm run build
cd ..
```

## 配置

### 1. 初始化配置向导

新用户推荐直接运行初始化向导，它会自动创建 `.env`，并引导你选择 Provider、消息通道和 dashboard 监听地址：

```bash
python -m hiclaw setup
```

检查当前配置是否满足启动条件：

```bash
python -m hiclaw doctor
```

### 1.1 后续补充配置

安装脚本会提供 `hiclaw` 命令，后续推荐优先使用：

```bash
hiclaw doctor
hiclaw setup
hiclaw model list
hiclaw channel setup telegram
```

如果服务器提示 `hiclaw: command not found`，通常是 `~/.local/bin` 没进 PATH：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

如果你在源码目录里，也可以直接运行：

```bash
cd ~/.hiclaw/hiclaw-py
python -m hiclaw doctor
```

常见补配置命令：

```bash
# 增加一个 OpenAI-compatible 模型服务
hiclaw model add --protocol openai --name deepseek --api-key xxx --base-url https://.../v1 --model deepseek-chat

# 增加一个 Anthropic-compatible 模型服务
hiclaw model add --protocol claude --name my-claude-gateway --api-key xxx --base-url https://... --model claude-sonnet-4

# 切换当前模型服务
hiclaw model use deepseek

# 后续再配置 Telegram / Feishu
hiclaw channel setup telegram
hiclaw channel setup feishu

# 关闭机器人通道，仅保留 TUI / dashboard
hiclaw channel setup none

# 搜索增强：可选；不配置也能回退到默认轻量搜索
hiclaw config set TAVILY_API_KEY=tvly-xxx
```

也可以用命令行直接写入配置，适合服务器或脚本化部署：

```bash
python -m hiclaw config set AGENT_PROVIDER=openai OPENAI_API_KEY=sk-xxx
python -m hiclaw config set TELEGRAM_BOT_TOKEN=xxx OWNER_ID=123456
python -m hiclaw config set HICLAW_DASHBOARD_HOST=0.0.0.0 HICLAW_DASHBOARD_PORT=8765
```

读取配置时默认会隐藏 `KEY` / `TOKEN` / `SECRET` / `PASSWORD` 类字段；排查问题时确实需要完整值，可以加 `--show-secrets`。

如果你只想本地调试，不需要 Telegram / Feishu，可以运行：

```bash
hiclaw-tui
```

### 2. 手动复制配置模板

Linux / macOS：

```bash
cp .env.example .env
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

### 3. 最小可运行配置

最少需要：

- 一个可用 Provider
- 一个可用入口

#### 方案 A：TUI 本地调试

只需配置 Provider：

```env
AGENT_PROVIDER=claude

ANTHROPIC_API_KEY=your_anthropic_api_key_here
ANTHROPIC_BASE_URL=
ANTHROPIC_MODEL=

WORKSPACE_DIR=./workspace
TAVILY_API_KEY=
```

说明：

- `hiclaw-tui` 不依赖 Telegram 或 Feishu
- `TAVILY_API_KEY` 是可选搜索增强；留空时会使用默认轻量搜索，质量、时效性和访问频率可能有限

#### 方案 B：Telegram Bot

```env
AGENT_PROVIDER=claude

TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
OWNER_ID=your_telegram_user_id_here

ANTHROPIC_API_KEY=your_anthropic_api_key_here
TAVILY_API_KEY=
WORKSPACE_DIR=./workspace
```

#### 方案 C：Feishu 长连接机器人

```env
AGENT_PROVIDER=openai

FEISHU_APP_ID=your_feishu_app_id_here
FEISHU_APP_SECRET=your_feishu_app_secret_here

OPENAI_API_KEY=your_openai_api_key_here
OPENAI_BASE_URL=
OPENAI_MODEL=gpt-4o-mini

TAVILY_API_KEY=
WORKSPACE_DIR=./workspace
```

### 3. 常用配置项

```env
# Dashboard
HICLAW_DASHBOARD_HOST=0.0.0.0
HICLAW_DASHBOARD_PORT=8765

# Scheduler
SCHEDULER_INTERVAL_SECONDS=30

# Tool trace
SHOW_TOOL_TRACE=0

# Session timeout
SESSION_TIMEOUT_SECONDS=86400

# Capability watcher
CAPABILITY_WATCHER_ENABLED=1
CAPABILITY_WATCHER_INTERVAL_SECONDS=1.0
```

说明：

- 云服务器上想从公网访问 dashboard，`HICLAW_DASHBOARD_HOST` 应设为 `0.0.0.0`
- `SHOW_TOOL_TRACE=1` 适合调试
- `TAVILY_API_KEY` 控制 `web_search` 能力；不需要联网搜索时可以留空

### 4. Cluster 配置

```env
AGENT_CLUSTER_ENABLED=0
AGENT_CLUSTER_REVIEW_ENABLED=1
AGENT_CLUSTER_ORCHESTRATOR_ENABLED=0
AGENT_CLUSTER_DYNAMIC_PLANNER_ENABLED=0
AGENT_CLUSTER_MAX_EVENTS=40
```

当前建议这样理解：

- `AGENT_CLUSTER_ENABLED=1`：启用 cluster foundation 与 dashboard 协作投影
- `AGENT_CLUSTER_REVIEW_ENABLED=1`：当计划命中相应任务类型时，加入 reviewer 角色
- `AGENT_CLUSTER_ORCHESTRATOR_ENABLED`：预留给更完整的真实多 Agent 执行链，当前不建议默认开启
- `AGENT_CLUSTER_DYNAMIC_PLANNER_ENABLED`：预留给动态任务规划能力，当前不建议默认开启

## 启动方式

### 方式 1：本地 TUI

```bash
hiclaw-tui
```

适合：

- 本地调试
- 不想配置 Telegram / Feishu
- 调试工具、workflow、memory

### 方式 2：前台运行

```bash
hiclaw run
```

或：

```bash
python -m hiclaw run
```

说明：

- 这会启动已配置的消息通道
- 也会同时启动 dashboard server
- 终端关闭后服务会停止
- 适合本地调试、Windows 原生环境，或交给 systemd/supervisor/docker 这类外部进程管理器托管
- 如果没有配置 Telegram 或 Feishu，这个入口会直接报错；此时请使用 `hiclaw-tui`

### 方式 3：后台运行

```bash
hiclaw start
```

查看后台状态：

```bash
hiclaw status
```

查看日志：

```bash
hiclaw logs
hiclaw logs -f
```

停止后台服务：

```bash
hiclaw stop
```

说明：

- `hiclaw start` 等价于仓库内的 `scripts/start.sh`
- `hiclaw stop` 等价于仓库内的 `scripts/stop.sh`
- 后台模式会写入 `data/hiclaw.pid` 和 `data/hiclaw.log`
- `hiclaw status` 检查 PID 是否还活着，并显示日志位置和 dashboard 地址
- `hiclaw logs -f` 用于实时查看后台日志
- 适合 Linux / macOS / WSL2 服务器长期运行

当前后台启动会做这些事：

- 运行 `hiclaw doctor` 做启动前配置检查
- 启动 HiClaw 主应用
- 检查 `.env` 中的 dashboard host / port
- 自动输出 dashboard 访问地址
- 检查 `/api/activity` 健康状态
- 检查 `/core` 健康状态
- 如果检测到 `pixel-office-core/package.json` 且系统有 npm，会自动构建 `pixel-office-core`

如果你在仓库源码目录里，也可以直接使用：

```bash
./scripts/start.sh
./scripts/stop.sh
```

### 方式 4：单独启动 Dashboard

```bash
hiclaw-dashboard
```

### 方式 5：只启动 Feishu 通道

```bash
hiclaw-feishu
```

## 访问地址

默认端口为 `8765`。

本地访问：

- `http://127.0.0.1:8765/`
- `http://127.0.0.1:8765/v2`
- `http://127.0.0.1:8765/core`

云服务器访问：

- 将 `HICLAW_DASHBOARD_HOST=0.0.0.0`
- 开放安全组 / 防火墙端口 `8765`

## 云服务器 Linux 部署教程

下面以 Ubuntu 服务器为例。

### 1. 准备系统环境

安装基础依赖：

```bash
sudo apt update
sudo apt install -y git curl python3 python3-venv python3-pip
```

如果你要使用 `/core` dashboard，建议额外安装 Node.js 和 npm：

```bash
sudo apt install -y nodejs npm
```

说明：

- 没有 npm 也可以启动主程序
- 但如果 `pixel-office-core/dist/` 丢失或你后续改了 core 前端，npm 可用于自动构建 `/core`

### 2. 拉取代码

```bash
git clone git@github.com:lianjiawei/hiclaw-py.git
cd hiclaw-py
```

如果你的服务器没有配置 SSH key，也可以使用 HTTPS：

```bash
git clone https://github.com/lianjiawei/hiclaw-py.git
cd hiclaw-py
```

### 3. 创建虚拟环境并安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .
```

如果需要语音识别：

```bash
python -m pip install -e ".[asr]"
```

### 4. 检查或继续修改 `.env`

推荐先运行：

```bash
python -m hiclaw setup
python -m hiclaw doctor
```

如果你手动编辑 `.env`，至少需要修改这些项：

```env
AGENT_PROVIDER=claude

ANTHROPIC_API_KEY=your_anthropic_api_key_here
ANTHROPIC_BASE_URL=
ANTHROPIC_MODEL=

TAVILY_API_KEY=

HICLAW_DASHBOARD_HOST=0.0.0.0
HICLAW_DASHBOARD_PORT=8765

WORKSPACE_DIR=./workspace
```

如果你使用 Telegram，还要配置：

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
OWNER_ID=your_telegram_user_id_here
```

如果你使用 Feishu，还要配置：

```env
FEISHU_APP_ID=your_feishu_app_id_here
FEISHU_APP_SECRET=your_feishu_app_secret_here
```

重要说明：

- `python -m hiclaw` 启动前会检查关键配置，缺少 Provider key 或消息通道时会给出修复建议
- 如果你只是想在服务器上先验证工具和模型，不配 Telegram / Feishu 时请用 `hiclaw-tui`
- 如果你要公网访问 dashboard，`HICLAW_DASHBOARD_HOST` 必须设为 `0.0.0.0`

### 5. 开放端口

你至少需要放行 dashboard 端口，例如 `8765`。

如果使用 Ubuntu UFW：

```bash
sudo ufw allow 8765/tcp
sudo ufw status
```

如果是云厂商服务器，还要在安全组里放行：

- `8765/tcp`：dashboard
- 如需 SSH，确保 `22/tcp` 已放行

### 6. 启动服务

推荐使用仓库自带脚本：

```bash
./scripts/start.sh
```

停止服务：

```bash
./scripts/stop.sh
```

`start.sh` 当前会：

- 运行当前 HiClaw 命令所在 Python 的 `hiclaw doctor` 做启动前配置检查
- 使用同一个 Python 启动 HiClaw 主进程
- 读取 `.env` 中的 dashboard host / port
- 打印公网访问地址
- 检查 `/api/activity` 健康状态
- 检查 `/core` 健康状态
- 检测到 npm 时自动构建 `pixel-office-core`

### 7. 查看访问地址

启动成功后通常会看到类似输出：

```text
Dashboard: http://<your-ip>:8765 (classic) | http://<your-ip>:8765/v2
Core Dashboard: http://<your-ip>:8765/core
```

常用页面：

- `http://<your-ip>:8765/`
- `http://<your-ip>:8765/v2`
- `http://<your-ip>:8765/core`

### 8. 查看日志

日志文件默认在：

```bash
data/hiclaw.log
```

实时查看：

```bash
tail -f data/hiclaw.log
```

### 9. 常见问题排查

#### 1. 启动后网页打不开

先检查：

- `.env` 里是否设置了 `HICLAW_DASHBOARD_HOST=0.0.0.0`
- 云服务器安全组是否放行 `8765`
- 系统防火墙是否放行 `8765`

再检查服务日志：

```bash
tail -n 100 data/hiclaw.log
```

#### 2. `/core` 页面能打开但素材加载失败

先更新到最新代码：

```bash
git pull origin master
```

如果你改过 `pixel-office-core`，可以手工重建：

```bash
cd pixel-office-core
npm ci
npm run build
cd ..
```

然后重启：

```bash
./scripts/stop.sh
./scripts/start.sh
```

#### 3. 联网搜索不生效

检查 `.env` 是否配置：

```env
TAVILY_API_KEY=your_tavily_api_key_here
```

`web_search` 会优先使用 Tavily；如果没有配置 `TAVILY_API_KEY`，会回退到系统默认轻量搜索。默认搜索能力可能受质量、时效性和访问频率限制。

#### 4. 主程序启动直接报错

通常是因为没有配置任何可用消息通道。

解决方式：

- 配置 Telegram 或 Feishu 后再运行 `python -m hiclaw`
- 或改为使用本地 `hiclaw-tui`

### 10. 更新代码

服务器更新推荐流程：

```bash
git pull origin master
source .venv/bin/activate
python -m pip install -e .
./scripts/stop.sh
./scripts/start.sh
```

如果 `pixel-office-core` 有变更且服务器已安装 npm，`start.sh` 会自动重建；也可以手工执行 `npm run build`。

## 联网搜索说明

联网搜索统一通过工具 `web_search` 完成。Tavily 是可选增强；配置 `TAVILY_API_KEY` 后会通过 HTTP 调用 Tavily，留空则回退到系统默认轻量搜索。

可选配置：

```env
TAVILY_API_KEY=your_tavily_api_key_here
TAVILY_SEARCH_DEPTH=basic
TAVILY_MAX_RESULTS=5
```

未配置 `TAVILY_API_KEY` 时，搜索仍会尝试使用默认轻量搜索，但结果质量、时效性和访问频率可能有限。

## Pixel Office Core 说明

`pixel-office-core/` 已作为仓库的一部分追踪上传，包含：

- `src/` 源码
- `dist/` 构建产物
- `public/assets/` 素材资源
- `hiclaw-dashboard.html/js` 入口文件

`/core` 页面直接由 `monitor/server.py` 映射为静态资源，不需要单独再启动一个 Node 服务。

只有在你单独开发 `pixel-office-core` 前端时，才需要手工进入该目录运行 npm 命令。

### 后续开发约束

`pixel-office-core` 的办公室渲染、素材组织、角色移动、状态表达和气泡逻辑，后续优化时应优先参考：

- https://github.com/pablodelucca/pixel-agents

这意味着后续优化 `/core` 时，优先遵循以下原则：

- 优先参考 `pixel-agents` 的既有素材组织和渲染分层
- 优先参考其角色移动、路径、站位、工位行为逻辑
- 优先参考其状态动画、气泡提示、家具交互和空间表达方式
- 在其成熟逻辑基础上做 HiClaw 的 `planner / executor / reviewer` 状态映射
- 尽量避免脱离参考项目而单独发明一整套新的办公室行为系统

也就是说，后续 `/core` 的优化方向应当是：

- 先对照 `pixel-agents`
- 再做稳定迁移
- 最后做 HiClaw 的多 Agent 状态适配

## 测试

运行测试：

```bash
python -m pytest test/ tests/ -q
```

当前测试覆盖重点包括：

- tool registry
- memory system
- session / decision / workflow 路由
- cluster runtime foundation

## 当前边界

稳定部分：

- 多通道入口
- 双 Provider 基础路由
- tool / workflow / skill registry
- memory / scheduler / dashboard
- cluster runtime foundation

演进中部分：

- planner / reviewer 的真实独立执行链
- 多 Agent 编排闭环
- task DAG / dependency scheduling
- 完整 cluster dashboard 可视化闭环

## 面向行业企业 Agent 的继续优化方向

如果你希望把当前项目继续演进成更适合行业和企业场景的 Agent 平台，建议重点从下面几个方向推进。

### 1. 安全与治理

当前已经有工具确认机制，但企业场景通常还需要继续加强：

- 更细粒度的权限模型：按用户、部门、通道、工具、目录、数据源分别授权
- 多级审批：高风险命令、外发消息、外部写入、批量文件修改走审批链
- 操作审计：完整记录谁在什么时间调用了什么工具、读写了什么资源
- 敏感信息保护：API Key、个人信息、合同文本、内部文档自动脱敏
- 沙箱执行：把 Bash、脚本、外部下载等高风险能力隔离到受控运行环境

### 2. 记忆系统升级

当前已有分层记忆，但企业 Agent 往往需要更强的长期记忆治理能力：

- 记忆分级：个人记忆、团队记忆、组织级知识分层隔离
- 生命周期管理：记忆创建、过期、归档、删除、回溯
- 可解释记忆：回答时标明用了哪段记忆、为什么命中
- 记忆冲突处理：旧知识与新知识冲突时的优先级和审查机制
- 任务记忆闭环：把长期项目中的关键结论、风险、待办沉淀为可复用状态

### 3. 知识库深度结合

企业 Agent 的价值很大程度上来自“能否真正理解企业知识”。建议后续重点补齐：

- 多数据源接入：本地文件、NAS、SharePoint、飞书文档、Confluence、数据库
- 文档解析增强：PDF、Word、Excel、扫描件、表格附件、制度文档
- RAG 检索链路：索引、召回、重排、片段引用、来源追踪
- 知识更新机制：文档变更后自动增量同步和索引刷新
- 知识权限隔离：不同用户只能检索自己有权访问的知识范围

### 4. 长时间任务执行

很多企业场景不是“一问一答”，而是数分钟、数小时甚至跨天执行的任务：

- 长任务状态机：排队、执行中、等待审批、等待外部系统、已完成、失败
- 断点续跑：进程重启后恢复上下文和任务进度
- 任务编排：把复杂任务拆成阶段、子任务和依赖关系
- 异步通知：任务完成、失败、需审批时主动通知 Telegram / Feishu / 邮件
- 结果归档：任务执行结果、附件、日志、审计记录统一沉淀

### 5. 多 Agent 协作深化

当前项目已经有 cluster runtime foundation，后续可以进一步变成真实企业协作系统：

- planner 真实任务拆解
- reviewer 独立复核链
- specialist agents：法务、财务、运维、数据分析、项目经理等角色化 Agent
- agent-to-agent message protocol
- 并行 executor 与任务优先级调度

### 6. 企业系统集成

行业场景里，Agent 只有真正接入业务系统后才有生产力：

- ERP / CRM / OA / 工单 / 合同 / 招采系统集成
- 数据库只读查询与受控写入
- 邮件、日历、IM、文档系统联动
- 企业身份体系对接：SSO、组织架构、角色同步
- 外部 API 编排：将业务操作沉淀为 workflows

### 7. 可观测性与运营

企业场景不仅要能跑，还要能被监控、分析和持续优化：

- 全链路 tracing：从用户请求到工具调用到最终结果
- 任务耗时、成功率、失败率统计
- 常见失败原因聚类
- 成本监控：模型调用、搜索调用、长任务资源消耗
- Dashboard 进一步演进为运维和运营双视图

### 8. 交付形态建议

如果面向行业客户交付，建议把当前仓库继续演进成以下层次：

- Agent Runtime Layer：保留当前执行内核
- Knowledge Layer：企业知识库与权限检索
- Workflow Layer：沉淀行业 SOP 和审批流
- Governance Layer：审计、安全、权限、合规
- Operations Layer：监控、告警、统计、版本发布

### 9. 推荐演进顺序

如果按投入产出比排序，建议优先级如下：

1. 安全与权限治理
2. 知识库与 RAG 深度结合
3. 长任务执行与断点恢复
4. 记忆系统升级
5. 多 Agent 真实协作闭环
6. 行业系统集成与运营面板

这样能让项目先具备企业可落地性，再逐步增强自治能力。

## 适合谁使用

这个项目适合：

- 想长期运行个人 Agent 的开发者
- 想做 tool registry / workflow / memory / scheduler / cluster runtime 的工程实践者
- 想把单 Agent 系统逐步演进成多 Agent 系统的团队
