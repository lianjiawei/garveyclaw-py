# garveyclaw-py

一个基于 Claude Agent SDK 和 Telegram Bot 的个人 Agent 项目。

当前项目已经具备这些能力：

- 通过 Telegram 接收消息并回复
- 使用 Claude Agent SDK 调用模型服务
- 支持常见 Markdown 在 Telegram 中的格式化显示
- 支持内置 Claude 工具和自定义 MCP 工具
- 支持工作区目录内的文件工具
- 支持只允许指定 owner 使用机器人
- 支持连续会话和会话重置
- 支持长期记忆文件和对话记录落盘
- 支持单次 / 每天 / 每周定时任务
- 支持简单自然语言创建定时任务

## 项目结构

```text
garveyclaw_py/
├─ pyproject.toml
├─ .env
├─ claw_course_bot.py
├─ COURSE_GUIDE.md
└─ src/
   └─ garveyclaw/
      ├─ __main__.py
      ├─ access.py
      ├─ agent_tools.py
      ├─ app.py
      ├─ claude_client.py
      ├─ config.py
      ├─ memory_store.py
      ├─ scheduler.py
      ├─ scheduler_store.py
      ├─ session_store.py
      ├─ telegram_bot.py
      └─ telegram_formatting.py
```

各模块职责如下：

- `app.py`：程序入口，初始化日志并启动 Telegram 轮询
- `config.py`：读取 `.env` 并提供运行配置
- `access.py`：处理 owner 身份判断
- `agent_tools.py`：定义工作区工具和 Telegram 发送工具
- `claude_client.py`：组装 Claude 运行时、工具、hooks 和模型调用逻辑
- `memory_store.py`：负责长期记忆文件和对话记录的读写
- `scheduler_store.py`：负责定时任务数据库初始化
- `scheduler.py`：负责定时任务解析、存储、调度执行与任务管理
- `session_store.py`：负责本地 session_id 的读写和清空
- `telegram_bot.py`：处理 Telegram 命令、消息和异常
- `telegram_formatting.py`：把常见 Markdown 转成 Telegram 可渲染的 HTML
- `claw_course_bot.py`：课程版单文件示例
- `COURSE_GUIDE.md`：课程版教材

## 环境准备

推荐使用独立的 Conda 环境：

```powershell
conda create -n garveyclaw python=3.12 -y
conda activate garveyclaw
python -m pip install -e .
```

## 环境变量

项目通过 `.env` 读取配置，至少需要这些变量：

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
OWNER_ID=your_telegram_user_id
ANTHROPIC_API_KEY=your_api_key
ANTHROPIC_BASE_URL=https://your-compatible-endpoint
ANTHROPIC_MODEL=qwen3.6-plus
```

可选变量：

```env
WORKSPACE_DIR=E:\AICode\AIProRepo\garveyclaw_py\workspace
```

说明：

- `TELEGRAM_BOT_TOKEN`：Telegram 机器人令牌
- `OWNER_ID`：允许使用机器人的 Telegram 用户 ID
- `ANTHROPIC_API_KEY`：模型服务鉴权 key
- `ANTHROPIC_BASE_URL`：兼容 Anthropic 协议的服务地址
- `ANTHROPIC_MODEL`：默认模型名
- `WORKSPACE_DIR`：工具默认工作的目录；不配置时默认使用项目根目录下的 `workspace/`

运行时还会自动在项目根目录创建：

- `data/garveyclaw_session.json`：用于保存当前连续会话的 `session_id`
- `data/garveyclaw_tasks.db`：用于保存定时任务

并在工作区目录下维护：

- `memory/CLAUDE.md`：长期记忆文件
- `memory/conversations/YYYY-MM-DD.jsonl`：按天归档的对话记录

## 启动方式

推荐方式：

```powershell
python -m garveyclaw
```

如果已经执行过 `pip install -e .`，也可以直接运行：

```powershell
garveyclaw
```

启动成功后终端会看到：

```text
Bot is running...
```

## 当前工具能力

当前正式工程已经接入这些工具：

- `get_current_time`：获取当前服务器本地时间
- `list_workspace_files`：列出工作区顶层文件和目录
- `read_workspace_file`：读取工作区内文本文件
- `send_message`：向当前 Telegram 会话额外发送一条消息

同时还开启了 Claude Code 内置工具集，可在白名单允许时使用：

- `Read`
- `Write`
- `Edit`
- `Glob`
- `Grep`
- `WebSearch`
- `WebFetch`
- `Bash`

## 定时任务能力

当前正式工程已经支持：

- `/schedule_in 秒数 任务内容`：命令式创建单次任务
- `/tasks`：查看当前待执行任务
- `/cancel 任务ID`：取消任务

同时也支持简单自然语言定时表达，例如：

- `30秒后提醒我喝水`
- `今晚8点提醒我开会`
- `明天早上9点提醒我整理日报`
- `每天下午3点提醒我站起来活动一下`
- `每周一早上9点提醒我开例会`

定时任务保存在：

- `data/garveyclaw_tasks.db`

## 权限说明

- 只有 `OWNER_ID` 对应的 Telegram 用户会被处理
- 其他用户发送 `/start` 或普通消息时，机器人会直接忽略
- 文件工具只允许访问工作区目录内的路径

## 会话说明

- 机器人会把当前会话的 `session_id` 保存到 `data/garveyclaw_session.json`
- 下一条消息会优先尝试恢复这个 session，从而实现连续会话
- 可以通过 `/reset` 清空本地保存的 session，下一条消息会从新会话开始

## 记忆说明

- 长期稳定信息保存在工作区下的 `memory/CLAUDE.md`
- 每轮对话会追加保存到 `memory/conversations/` 下按日期命名的 `jsonl` 文件
- 可以通过 `/memory` 查看当前长期记忆内容
- 可以通过 `/remember 你的内容` 追加一条长期记忆

## 异常处理

当前项目已经补了基础异常处理：

- 模型调用失败时，会给用户返回友好的失败提示
- Telegram 格式化发送失败时，会自动回退成纯文本
- Telegram 网络或 API 异常会记录日志，并提示稍后重试
- 未捕获异常会进入全局错误处理器，便于排查

## 日志说明

当前默认日志策略偏安静，主要保留警告和错误：

- 正常轮询过程不会持续刷 `httpx` 请求日志
- 模型调用失败、Telegram 发送失败等问题仍会输出日志
- 启动成功时会输出 `Bot is running...`

## 停止运行

本地开发时，直接在终端按 `Ctrl + C` 即可停止机器人。

## VS Code 提示

项目使用 `src` 布局。为了让 VS Code 的跳转定义、自动补全更稳定，仓库里已经提供了：

- `.vscode/settings.json`

如果 VS Code 没有正确识别解释器，请手动选择你的 Conda 环境，例如：

```text
D:\anaconda3\envs\garveyclaw\python.exe
```
