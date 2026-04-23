# Claw 课程版教材

这份教材配套的主代码文件是 [claw_course_bot.py](/E:/AICode/AIProRepo/garveyclaw_py/claw_course_bot.py)。

它的定位不是正式工程入口，而是一份“单文件课程版”示例，目的是把下面这些能力收拢到一起，便于学习、复习和培训：

- Telegram 机器人基础收发
- owner 权限控制
- Claude Agent SDK 调用
- 连续会话 session
- 长期记忆与对话记录
- Claude 内置工具与自定义工具
- 单次 / 每天 / 每周定时任务
- 简单自然语言定时解析
- 全局锁串行化执行


## 1. 课程目标

学完这份课程版代码，你应该能理解下面这些问题：

- 一个 Telegram 机器人最基本的启动链路是什么
- Claude Agent SDK 如何接进 Telegram
- 为什么要有 owner 限制
- session 和 memory 有什么区别
- 工具是怎么注册、调用和显性展示的
- 定时任务如何从“自然语言”变成“数据库里的任务记录”
- 调度器如何周期性检查并执行任务
- 为什么要用全局锁避免多个 Agent 同时运行


## 2. 环境要求

### 2.1 Python

项目要求：

- Python `>= 3.12`

建议直接使用 Conda 单独建环境，不要混用项目里的旧 `.venv`。

### 2.2 Conda

推荐命令：

```powershell
conda create -n garveyclaw python=3.12 -y
conda activate garveyclaw
```

### 2.3 依赖安装

当前项目在 [pyproject.toml](/E:/AICode/AIProRepo/garveyclaw_py/pyproject.toml) 里声明的关键依赖有：

- `python-telegram-bot>=22.6`
- `claude-agent-sdk>=0.1.31`
- `python-dotenv>=1.2.1`
- `aiosqlite>=0.22.1`
- `apscheduler>=3.10,<4.0`
- `croniter>=6.0.0`

推荐在项目根目录执行：

```powershell
python -m pip install -e .
```

如果你只是想跑课程版文件，也可以按需安装最小依赖，但最省事的方式仍然是 `pip install -e .`。


## 3. 环境变量

课程版代码会读取 `.env`。至少需要这些变量：

```env
TELEGRAM_BOT_TOKEN=你的 Telegram Bot Token
ANTHROPIC_API_KEY=你的 Claude / 兼容服务 Key
ANTHROPIC_BASE_URL=你的接口地址
OWNER_ID=你的 Telegram 用户 ID
```

说明：

- `TELEGRAM_BOT_TOKEN`：Telegram 机器人令牌
- `ANTHROPIC_API_KEY`：Claude Agent SDK 调用所需密钥
- `ANTHROPIC_BASE_URL`：如果你用了代理服务或兼容服务，需要显式配置
- `OWNER_ID`：限制只有你本人能驱动这个机器人


## 4. 如何启动课程版

在项目根目录执行：

```powershell
python claw_course_bot.py
```

启动后你会在终端看到这些路径信息：

- 工作区目录
- session 文件
- 定时任务数据库
- 长期记忆文件
- 对话记录目录


## 5. 课程版文件结构

课程版代码主文件：

- [claw_course_bot.py](/E:/AICode/AIProRepo/garveyclaw_py/claw_course_bot.py)

运行后会使用这些目录和文件：

- 工作区目录：
  [workspace_course](/E:/AICode/AIProRepo/garveyclaw_py/workspace_course)
- 长期记忆文件：
  [workspace_course/memory_course/CLAUDE.md](/E:/AICode/AIProRepo/garveyclaw_py/workspace_course/memory_course/CLAUDE.md)
- 对话记录目录：
  [workspace_course/memory_course/conversations](/E:/AICode/AIProRepo/garveyclaw_py/workspace_course/memory_course/conversations)
- session 文件：
  [data/course_session.json](/E:/AICode/AIProRepo/garveyclaw_py/data/course_session.json)
- 定时任务数据库：
  [data/course_tasks.db](/E:/AICode/AIProRepo/garveyclaw_py/data/course_tasks.db)


## 6. 代码模块讲解

课程版虽然只有一个文件，但内部已经按功能分区整理好了。

### 6.1 基础配置

这一段主要做：

- 读取 `.env`
- 取出 Telegram Token、Claude Key、Owner ID

核心变量：

- `TELEGRAM_BOT_TOKEN`
- `ANTHROPIC_API_KEY`
- `ANTHROPIC_BASE_URL`
- `OWNER_ID`

### 6.2 路径与目录

这一段负责统一运行时目录，避免文件乱放。

重点路径：

- `DATA_DIR`
- `WORKSPACE_DIR`
- `MEMORY_DIR`
- `SESSION_FILE`
- `TASK_DB_FILE`
- `CLAUDE_MEMORY_FILE`

这一步很重要，因为后面的 session、memory、scheduler 都依赖这些路径。

### 6.3 运行期常量

这里定义了：

- `SCHEDULER_INTERVAL_SECONDS`
- `AGENT_LOCK`
- `SCHEDULER`

其中最重要的是：

- `AGENT_LOCK = asyncio.Lock()`

它的作用是把普通消息处理和定时任务执行都串行化，避免多个 Agent 同时跑起来，让学习过程更清楚。

### 6.4 数据结构

课程版里有一个很关键的小结构：

- `ParsedSchedule`

它把自然语言定时解析结果统一成：

- `run_at`
- `prompt`
- `schedule_type`
- `schedule_value`

这样后面不管是“30秒后”还是“每周一早上9点”，都能走同一条任务创建逻辑。

### 6.5 初始化辅助

这里主要做两件事：

- 创建示例文件 `demo.txt`
- 创建默认长期记忆文件 `CLAUDE.md`

其中 `CLAUDE.md` 会写明：

- 当前目录结构
- 什么应该写到长期记忆
- 什么只写入对话记录

### 6.6 通用辅助

这部分是一些小工具函数，例如：

- `is_owner(update)`
- `resolve_workspace_path(relative_path)`
- `normalize_hour(period, hour)`
- `compute_next_weekday_run(...)`

重点理解：

- `is_owner()`：把权限逻辑独立出来，避免在 handler 里到处重复判断
- `resolve_workspace_path()`：限制文件工具只能操作工作区，避免越界访问

### 6.7 自然语言定时解析

这是课程版里很适合讲的一个模块。

它分成四类解析：

- `parse_relative_schedule`
  处理“30秒后提醒我……”
- `parse_daily_schedule`
  处理“每天下午3点提醒我……”
- `parse_weekly_schedule`
  处理“每周一早上9点提醒我……”
- `parse_absolute_schedule`
  处理“今晚8点提醒我……”或“明天早上9点提醒我……”

最后再统一交给：

- `parse_natural_schedule`

这是一种很典型的“先分解，再汇总”的设计方式，培训时很适合讲。

### 6.8 Session 与长期记忆

这里你要区分两类状态：

#### session

通过：

- `load_session_id()`
- `save_session_id()`
- `clear_session_id()`

管理 [data/course_session.json](/E:/AICode/AIProRepo/garveyclaw_py/data/course_session.json)。

session 的作用是：

- 保持当前连续会话上下文
- `/reset` 时可以清掉

#### memory

通过：

- `load_long_term_memory()`
- `append_long_term_memory()`
- `append_conversation_record()`

管理长期记忆和按天的对话归档。

memory 的作用是：

- 保存跨会话稳定信息
- 保存对话原始记录

### 6.9 定时任务数据库

课程版把任务存进 SQLite，而不是只放内存里。

核心函数：

- `init_task_db()`
- `create_scheduled_task()`
- `list_scheduled_tasks()`
- `get_due_tasks()`
- `update_task_after_run()`
- `cancel_scheduled_task()`

为什么要用数据库：

- 机器人重启后任务不会丢
- 可以方便列出、取消、更新任务

### 6.10 工具层

课程版同时展示了两类工具：

#### 自定义工具

- `get_current_time`
- `list_workspace_files`
- `read_workspace_file`
- `send_message`

#### Claude 内置工具

通过：

```python
tools={"type": "preset", "preset": "claude_code"}
```

启用 `Read / Write / Edit / Bash / WebFetch` 等能力。

### 6.11 Agent 调用层

这里是 Telegram 和 Claude Agent SDK 真正对接的核心。

关键函数：

- `build_system_prompt()`
- `make_prompt_stream()`
- `run_agent()`
- `ask_claude()`

这里最值得讲的点：

#### 为什么 `system_prompt` 要读 `CLAUDE.md`

因为这样长期记忆会在每轮都注入给模型。

#### 为什么要有 `make_prompt_stream`

因为启用了 `can_use_tool` 后，Claude Agent SDK 要求 `prompt` 必须是 `AsyncIterable`，不能直接传普通字符串。

这是你前面在学习阶段踩过并解决过的关键兼容点。

### 6.12 调度器执行层

定时任务真正触发时，会走这里。

关键函数：

- `compute_next_run_after_execution()`
- `execute_scheduled_task()`
- `check_due_tasks()`
- `setup_scheduler()`

理解重点：

- `check_due_tasks()` 负责周期性扫库
- `execute_scheduled_task()` 负责真正执行任务
- 单次任务执行后会变成 `completed`
- `daily / weekly` 任务执行后会自动计算下一次时间并继续保持 `active`

### 6.13 Telegram 命令与消息入口

这一层是面向用户的入口。

支持的命令：

- `/start`
- `/schedule_in`
- `/tasks`
- `/cancel`
- `/reset`
- `/memory`
- `/remember`

普通文本消息会先做一件事：

- 尝试按自然语言解析成定时任务

如果识别失败，才会进入普通 Claude 对话链路。


## 7. 课程版支持的能力

### 7.1 owner 限制

只有 `.env` 里的 `OWNER_ID` 对应用户能驱动机器人。

其他人即使给 bot 发消息，也会被忽略。

### 7.2 连续会话

session 保存在 [data/course_session.json](/E:/AICode/AIProRepo/garveyclaw_py/data/course_session.json)。

这样下一轮消息还能延续前文。

### 7.3 长期记忆

长期记忆文件：

- [workspace_course/memory_course/CLAUDE.md](/E:/AICode/AIProRepo/garveyclaw_py/workspace_course/memory_course/CLAUDE.md)

原始对话记录：

- [workspace_course/memory_course/conversations](/E:/AICode/AIProRepo/garveyclaw_py/workspace_course/memory_course/conversations)

### 7.4 自定义工具

课程版可演示：

- 获取时间
- 列工作区文件
- 读工作区文件
- 让模型主动发 Telegram 消息

### 7.5 定时任务

支持：

- 单次任务
- 每天任务
- 每周任务

### 7.6 自然语言定时

支持这类表达：

- `30秒后提醒我喝水`
- `今晚8点提醒我开会`
- `明天早上9点提醒我整理日报`
- `每天下午3点提醒我喝水`
- `每周一早上9点提醒我开例会`


## 8. 运行与测试清单

### 8.1 启动

```powershell
python claw_course_bot.py
```

### 8.2 基础联调

按下面顺序测最合适：

1. `/start`
2. `现在几点了？`
3. `列出工作区里的文件`
4. `读取 demo.txt 的内容`

### 8.3 Session 测试

1. 连续发两条有上下文依赖的消息
2. 确认第二条能接住前文
3. 执行 `/reset`
4. 再发依赖短期上下文的问题，确认它已经从新会话开始

### 8.4 Memory 测试

1. `/memory`
2. `/remember 我喜欢中文回答`
3. 再次 `/memory`
4. 打开 [workspace_course/memory_course/CLAUDE.md](/E:/AICode/AIProRepo/garveyclaw_py/workspace_course/memory_course/CLAUDE.md) 检查是否写入

### 8.5 单次定时任务测试

1. `/schedule_in 30 30秒后提醒我喝水`
2. `/tasks`
3. 等到触发

### 8.6 自然语言定时测试

直接发送：

- `30秒后提醒我喝水`
- `今晚8点提醒我开会`
- `每天下午3点提醒我喝水`
- `每周一早上9点提醒我开例会`

### 8.7 数据落盘检查

重点看这几个文件：

- [data/course_session.json](/E:/AICode/AIProRepo/garveyclaw_py/data/course_session.json)
- [data/course_tasks.db](/E:/AICode/AIProRepo/garveyclaw_py/data/course_tasks.db)
- [workspace_course/memory_course/CLAUDE.md](/E:/AICode/AIProRepo/garveyclaw_py/workspace_course/memory_course/CLAUDE.md)
- [workspace_course/memory_course/conversations](/E:/AICode/AIProRepo/garveyclaw_py/workspace_course/memory_course/conversations)


## 9. 课程讲解建议

如果你拿这份代码做培训，推荐按这个顺序讲：

### 第一阶段：让机器人先跑起来

- 环境变量
- Telegram Application
- `/start`
- 普通消息收发

### 第二阶段：加入 Claude

- `ask_claude()`
- `run_agent()`
- `system_prompt`

### 第三阶段：加入权限与状态

- `is_owner()`
- `session`
- `memory`

### 第四阶段：加入工具

- 自定义工具
- MCP server
- Claude 内置工具
- hooks 显性化

### 第五阶段：加入定时任务

- 自然语言解析
- SQLite 持久化
- scheduler 周期检查
- 执行后更新下一次时间

### 第六阶段：讲整体架构

- Telegram 是入口
- Claude 是智能体核心
- tools 是执行层
- session / memory 是状态层
- scheduler 是时间驱动层


## 10. 与正式工程的关系

课程版文件：

- [claw_course_bot.py](/E:/AICode/AIProRepo/garveyclaw_py/claw_course_bot.py)

正式工程入口：

- [src/garveyclaw/app.py](/E:/AICode/AIProRepo/garveyclaw_py/src/garveyclaw/app.py)

课程版的作用是：

- 让你用一份单文件理解整体能力
- 方便复习
- 方便培训
- 功能调通后，再迁回正式工程的模块化结构

也就是说：

- 课程版适合“讲原理、看全局”
- 正式工程适合“真实维护、持续扩展”


## 11. 一句话总结

如果你只看一份文件来理解这个项目，优先看：

- [claw_course_bot.py](/E:/AICode/AIProRepo/garveyclaw_py/claw_course_bot.py)

如果你要真正维护项目，再回到：

- [src/garveyclaw](/E:/AICode/AIProRepo/garveyclaw_py/src/garveyclaw)
