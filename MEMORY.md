# GarveyClaw 项目记忆

## 当前项目状态

- 正式运行入口是 `python -m garveyclaw`
- 主包目录是 `src/garveyclaw`
- 当前正式工程已经拆成这些模块：
  - `app.py`
  - `config.py`
  - `claude_client.py`
  - `telegram_bot.py`
  - `telegram_formatting.py`
  - `__main__.py`
- `README.md` 已补充了基础运行说明
- VS Code 跳转问题已经通过 `.vscode/settings.json` 和 Conda 解释器选择解决

## 环境约定

- 当前主要使用 Conda 环境：`garveyclaw`
- Python 路径示例：使用当前 Conda 环境里的 `python`，不要在文档中写入本机绝对路径
- 现在不再使用项目里的 `.venv`

## ep2 的定位

- `ep2.py` 是“参考学习版”
- 不作为真实入口运行
- 里面保留了较详细的中文注释，便于理解 Telegram + Claude SDK 的基础链路

## ep3 的定位

- `ep3.py` 是“工具学习版”
- 用户明确要求：在没有再次明确允许前，只在 `ep3.py` 里实验工具功能，不要把工具实现迁回正式工程
- `ep3.py` 目前用于学习：
  - Claude 内置工具显性化
  - 自定义 MCP 工具
  - `allowed_tools`
  - `can_use_tool`
  - `cwd`
  - 工作区目录限制

## ep3 当前已经实现的内容

- 创建了学习工作区：`workspace_ep3/`
- 提供了示例文件：`workspace_ep3/demo.txt`
- 自定义工具：
  - `get_current_time`
  - `list_workspace_files`
  - `read_workspace_file`
  - `send_message`
- `send_message` 是会话绑定工具：
  - 依赖 Telegram 的 `bot`
  - 依赖当前 `chat_id`
  - 通过 `build_learning_mcp_server(bot, chat_id)` 闭包注入
- 已使用 `create_sdk_mcp_server(...)` 注册学习版 MCP server
- 已开启内置 Claude Code 工具集
- 已通过 hooks 把工具调用过程显性显示到 Telegram

## ep3 里已经确认的关键结论

### 1. `cwd` 的作用

- `cwd` 是当前工作目录
- 它决定 Claude 处理相对路径时默认以哪个目录为起点
- 但 `cwd` 不是绝对安全边界
- 真正的路径安全还要靠工具层自己校验

### 2. `send_message` 工具的性质

- 它和其他自定义工具一样可以暴露给 Claude
- 但它属于“动作型 / 有副作用 / 会话绑定”工具
- 学习版里这样写是合适的
- 正式工程里后面可能要进一步抽象

### 3. `can_use_tool` 与流式 prompt 的兼容点

- 这是一个非常重要的已确认问题
- 只要启用了 `can_use_tool`
- `query(...)` 的 `prompt` 就不能直接传普通字符串
- 必须改成 `AsyncIterable`
- 在 `ep3.py` 里已经通过 `make_prompt_stream(text)` 解决
- 这个兼容点后面迁回正式工程时很可能还会再次遇到

### 4. 学 `allowed_tools` 时不要同时开 `can_use_tool`

- 之前已经验证：
  - `can_use_tool=allow_all_tools` 会把动态权限全部放行
  - 这样会掩盖 `allowed_tools` 的学习效果
- 所以目前 `ep3.py` 已切成“先只学 `allowed_tools`”的状态
- 当前版本里已经去掉了 `can_use_tool`

### 5. MCP 工具实际显示名

- Telegram 中观察到的工具名类似：
  - `mcp__learning__get_current_time`
  - `mcp__learning__send_message`
- 这说明 MCP 工具在运行时会带 server 前缀
- 后续如果继续深究权限或白名单匹配，需要留意“工具注册名”和“实际运行时名”之间的关系

## 日志与异常处理现状

- 正式工程的日志已经做过一轮收敛
- 正常运行时不会像最开始那样刷很多请求日志
- 但“启动时断网导致 Telegram polling 打出 traceback”这个问题还没有彻底解决
- 用户已明确表示这个问题暂时先放下，不继续投入时间

## 下次继续时建议优先级

1. 继续只在 `ep3.py` 学完工具机制
2. 分开验证：
   - `allowed_tools`
   - `can_use_tool`
   - MCP 工具命名
   - 工具失败时的可见反馈
3. 等用户明确允许后，再把稳定方案迁回正式工程
4. 正式工程后续高优先级方向：
   - 工具运行层抽象
   - 工作区文件工具
   - 会话上下文记忆
   - 更清晰的工具状态展示

## 本次停下时的直接上下文

- 用户刚刚在学习 `allowed_tools`
- 已经观察到：
  - 去掉 `can_use_tool` 后
  - `allowed_tools` 的效果开始变明显
- 用户还在继续理解：
  - 工具为什么会出现 `[Tool Start]`
  - 为什么有时会尝试但不会真正成功执行

## 与用户协作时要记住的偏好

- 用户喜欢中文讲解和中文关键注释
- 后续新增注释必须写成正常中文 UTF-8，不要留下问号乱码、mojibake 或终端编码污染过的注释
- 后续新增或调整环境变量时，优先修改真实 `.env`，确认无误后再同步 `.env.example`
- 用户希望学习版代码尽量聚焦主线，不要塞太多分段、Markdown 渲染之类的辅助逻辑
- 用户明确要求：没有再次允许前，不要把 `ep3.py` 的工具实验迁移到正式工程文件
