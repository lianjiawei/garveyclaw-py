# Bash 工具跨平台执行规范



## 1. 目标



将注册表中名为 `bash` 的工具改为在真实宿主平台上执行命令，而非始终调用 PowerShell。



修改必须保持现有能力模型不变：



- 一个共享的 `ToolSpec`

- 一条共享的 `execute_tool()` 执行路径

- 一个对外工具名：`bash`

- Claude 和 OpenAI 两套适配器行为一致



---



## 2. 背景



当前实现（`src/weclaw/capabilities/tools.py`）始终执行：



```python

["powershell", "-NoProfile", "-Command", command]

```



这产生了三层平台不匹配：



### 2.1 运行时不匹配



- Linux/macOS 宿主可能没有安装 `powershell`

- 即使安装了，PowerShell 也不是 Unix 环境命令预期的原生 shell



### 2.2 语义不匹配



- 工具名叫 `bash`，实际执行的是 Windows PowerShell

- Agent 指令必须围绕实现细节做补偿，而非基于真实的工具契约



### 2.3 Prompt 漂移与路由风险



- 决策层可能正确地选择了工具执行路径

- Provider 生成了适合真实宿主平台的命令

- 但工具执行器仍绑定在错误的 shell 上，导致规划成功后执行失败



---



## 3. 目标与约束



### 3.1 主要目标



1. 使用真实宿主平台执行命令

2. 保持工具名 `bash` 以向后兼容

3. 最小化共享工具层之外的改动

4. 使执行契约足够真实，以便简化并对齐 Provider Prompt



### 3.2 次要目标



1. 在工具输出中显式展示使用的 shell，便于调试

2. 保持 `workdir`、超时、输出截断行为不变

3. 不引入第二个 shell 工具（如 `powershell`）



### 3.3 非目标



本阶段不做：



1. 重新设计工具注册表

2. 引入命令沙箱

3. 执行前校验/审查 shell 命令

4. 跨 Windows/Unix 统一 shell 语法

5. 将工具从 `bash` 改名



### 3.4 架构约束



方案必须适配现有架构：



- 共享执行入口：`execute_tool()`

- Claude MCP 和 OpenAI function calling 的 Provider 投影

- `ToolSpec` 上已挂载确认策略

- TUI / Telegram / 飞书共用同一工具行为



---



## 4. 方案设计



### 4.1 宿主原生 Shell 分发



`bash` 工具按运行时平台分支选择 shell。



#### Unix-like 宿主



当 `os.name != "nt"` 时，使用 `shutil.which("bash")` 动态查找 Bash 路径：



```python

import shutil

bash_path = shutil.which("bash")  # 返回 "/bin/bash" 或 "/usr/bin/bash" 等

```



如果找不到 `bash`，返回明确的工具错误而非静默降级为 `sh`。



**选择理由**：

- 匹配工具名称

- 匹配常见 Linux/macOS 命令预期

- 支持管道、重定向、glob、内联变量等 shell 特性



**使用 `shutil.which` 而非硬编码路径的理由**：

- Shell 位置因发行版差异很大：`/bin/bash`、`/usr/bin/bash`、NixOS 在 `/run/current-system/sw/bin/bash`、Termux 在 `/data/data/com.termux/files/usr/bin/bash`

- 动态查找覆盖更广，无需维护路径白名单



**macOS 特别说明**：macOS 默认 shell 是 zsh。`bash -l` 加载的配置文件是 `.bash_profile`/`.bash_login`/`.profile`（按序查找第一个），而非 zsh 的 `.zshrc`。如需加载 zsh 配置应自行在命令中处理。这不属于 bug，但需在文档中明示。



#### Windows 宿主



当 `os.name == "nt"` 时，执行：



```text

powershell -NoProfile -Command <command>

```



保持现有 Windows 行为不变。



### 4.2 工具名不变



本阶段保持工具名为 `bash`：



1. 避免破坏 Provider allowlist、工作流定义和 Agent 习惯

2. 真正的缺陷不是名称本身，而是名称与运行时行为的不匹配

3. 修改后名称可接受：Unix 宿主真正使用 Bash，Windows 宿主显式使用 PowerShell



### 4.3 输出格式增加 Shell 元数据



工具输出文本需包含使用的 shell 信息，推荐格式：



Unix：

```text

Shell: bash (/usr/bin/bash)

Workdir: /path/to/dir

Exit code: 0



STDOUT:

...

```



Windows：

```text

Shell: powershell

Workdir: C:\path\to\dir

Exit code: 0



STDOUT:

...

```



这减少了调试歧义，帮助用户和未来 model prompt 从真实执行器行为进行推理。



### 4.4 错误报告规范



Shell 启动失败必须成为明确的工具错误：



- 找不到 shell 可执行文件：`bash executable not found on this host`

- Windows 找不到 PowerShell：`powershell executable not found on this host`

- 超时：`command execution timed out (60 seconds)`



优于泛泛的堆栈跟踪或不透明的 subprocess 失败。



### 4.5 Prompt 对齐



#### 新的 Prompt 事实



修改后 Prompt 应表达：



1. `bash` 工具运行在宿主的原生 shell 上

2. 在 Linux/macOS 上，生成 Bash 兼容的命令

3. 在 Windows 上，生成 PowerShell 兼容的命令

4. 不要在一个平台上假设另一个 shell 家族的语法



#### 删除的内容



当前 prompt 过度强调 Windows/PowerShell，应改为平台条件式引导。具体操作：



- **删除**："不确定环境时，先执行 `$PSVersionTable` 判断，有输出就是 Windows"

- **保留**：Windows 下 PowerShell 等效命令的速查表（Move-Item、Remove-Item 等仍有参考价值）

- **替换为**："当前系统已按操作系统自动选择 shell，在 Linux/macOS 上直接编写 Bash 命令，在 Windows 上编写 PowerShell 命令"



#### 外部 rules.md 文件迁移提醒



`claude.py` 允许从 `workspace/prompts/rules.md` 加载外部 prompt 片段（该文件优先于内联规则）。如果用户自定义了 `rules.md` 且里面仍有旧的 `$PSVersionTable` 判断指令，系统 prompt 虽已修正但外部文件会覆盖，导致 agent 仍被误导。



**操作**：修改完成后提醒用户检查 `workspace/prompts/rules.md`（如果存在），将其中的 PowerShell-first 指令更新为平台自适应引导。



### 4.6 决策层无需变更



决策层在本阶段不需要策略变更：



- 决策问题已正确建模为工具执行路径

- 缺陷在于执行器真实性，而非路由策略



只有当未来遥测数据显示特定 shell 失败模式时，决策层才应考虑在某些宿主上对 `bash` 做偏差调整。



---



## 5. 详细行为要求



### 5.1 输入要求



- `command`：必填

- `workdir`：可选，按 workspace 策略解析

- `timeout`：可选，保持当前默认行为



### 5.2 执行要求



1. 与现有行为一致地解析 `workdir`

2. 按宿主平台选择 shell

3. 通过该 shell 执行一条命令字符串

4. 保持超时处理

5. 捕获 stdout 和 stderr

6. 返回统一文本结果



### 5.3 输出要求



结果文本需包含：



1. 使用的 shell 名称

2. 有效工作目录

3. 退出码

4. stdout（非空时）

5. stderr（非空时）

6. 大输出截断时有告知提示



### 5.4 错误要求



以下情况应返回工具错误：



1. `command` 为空

2. `workdir` 逃逸 workspace 策略

3. shell 二进制不可用

4. subprocess 超时

5. subprocess 启动失败



---



## 6. 兼容性分析



### 6.1 向后兼容



| 项目 | 状态 |

|------|------|

| 工具名 `bash` | 不变 |

| 工具 schema（`command`, `workdir`, `timeout`） | 不变 |

| 风险级别和确认策略 | 不变 |

| Provider 集成结构 | 不变 |

| 工作流对 `bash` 的引用 | 不变 |



### 6.2 变更项



| 项目 | 变更 | 风险 |

|------|------|------|

| Unix 宿主上的实际 shell | PowerShell → Bash | 修复 |

| 输出文本格式 | 增加 `Shell:` / `Workdir:` 头 | 低：下游不结构化解析输出 |



### 6.3 工作流兼容性



现有调用 `bash` 的工作流保持结构有效。



**风险**：某些工作流可能在旧行为下编写了 PowerShell 语法命令。修复后这些命令只在 Windows 上有效，这是正确的行为修正。



### 6.4 Prompt 兼容性



Provider prompt 必须在同一变更集中更新，否则运行时和 prompt 将朝相反方向漂移。



---



## 7. 替代方案



| 方案 | 结论 | 理由 |

|------|------|------|

| A：保持全局 PowerShell | ❌ 拒绝 | 仍然不符合宿主平台，Linux/macOS 会失败 |

| B：将工具改名为 `shell` | 本阶段拒绝 | 长期更清晰，但引入兼容性开销，当前不需要 |

| C：同时提供 `bash` 和 `powershell` 两个工具 | 本阶段拒绝 | 向模型层暴露平台分支，增加注册表复杂度 |

| D：Unix 上从 Bash 降级为 `sh` | 拒绝 | 静默降级导致 shell 特性差异，调试更困难 |



---



## 8. 风险评估



### 风险 1：已有 PowerShell 命令习惯



某些 prompt、skill 文件或用户编写的工作流可能隐式依赖 PowerShell 语法。修复后在 Linux 上这些命令会失败。这不是系统正确性的回退，但有迁移风险。



**缓解**：同一变更中更新 prompt，明确文档化宿主原生 shell 行为。



### 风险 2：输出格式漂移



增加 `Shell:` 和 `Workdir:` 行改变了原始工具输出文本。



**缓解**：保持格式简单稳定，不改变 `STDOUT` 和 `STDERR` 的语义含义。



**待确认**：检查 dashboard 是否有结构化解析 bash 工具输出的逻辑（如正则匹配 `退出码:`）。如有，需要同步更新。



### 风险 3：精简系统上 Bash 不可用



部分精简 Unix 环境可能未安装 Bash。



**缓解**：`shutil.which("bash")` 动态查找，找不到则明确报错。



### 风险 4：`bash -l` 启动时间开销



`bash -l` 会加载 profile 文件（`/etc/profile`、`~/.bash_profile` 等），比 `bash -c` 慢。如果 profile 中有耗时初始化（如 nvm、conda 自动激活），每次调用额外增加几十到几百毫秒。



**当前态度**：可接受。如未来成为问题，可改为 `/bin/bash -c` 或通过配置项控制。



### 风险 5：`workdir` 与 `bash -lc` 的交互



`bash -lc` 先加载 profile 再切换 `cwd` 到目标目录。如果 bash profile 脚本中有 `cd` 操作或依赖 `PWD` 的初始化逻辑，可能会覆盖 `cwd` 设置。



**影响**：低。正常 bash profile 不会 `cd`。可在测试中增加 `pwd` 验证。



### 风险 6：特殊字符注入



`subprocess.run` 将 `command` 作为单个字符串传给 shell。如果 command 包含 null 字节（`\x00`），Linux 上 `execve` 会拒绝并抛出 `ValueError`。



**影响**：极低。正常用户和 agent 不会生成含 null 字节的命令。



---



## 9. 实施计划



### Phase 1：执行器修正



修改 `src/weclaw/capabilities/tools.py`：



1. 检测宿主平台

2. 相应选择 shell 命令

3. 在结果文本中增加 shell 元数据

4. 保持超时和捕获语义

5. 增加 shell 不可用时的错误处理



### Phase 2：Prompt 对齐



更新：

- `src/weclaw/agents/claude.py`

- `src/weclaw/agents/openai.py`



描述宿主原生 shell 行为，替代 PowerShell-first 行为。



### Phase 3：验证



验证：

1. Linux 宿主上 Bash 命令执行正常

2. Windows 宿主上 PowerShell 命令执行正常

3. 无效 shell 场景错误处理正确

4. 超时场景正确



---



## 10. 验收标准



1. 在 Linux/macOS 上，`bash` 工具通过 Bash 而非 PowerShell 执行

2. 在 Windows 上，`bash` 工具继续通过 PowerShell 执行

3. 工具输出明确声明使用的 shell

4. Provider prompt 不再在非 Windows 宿主上暗示 PowerShell-first 行为

5. 现有工作流和工具注册结构兼容

6. Shell 启动失败产生明确的、用户可读的工具错误



---



## 11. 后续问题



以下问题本阶段暂不处理：



1. 工具是否最终应从 `bash` 改名为 `shell`？

2. shell 可用性是否应纳入 `ToolSpec.availability`？

3. 工作流定义是否应增加可选的宿主约束？

4. 系统是否应在决策追踪或上下文快照中暴露宿主操作系统？



---



## 12. 建议



执行窄范围的修正：



1. 修复执行器为宿主原生

2. 立即对齐 prompt

3. 本阶段不改工具名

4. 不扩展沙箱或多工具 shell 设计



以最小架构扰动换取真实的运行时契约。



---



## 13. 实施分析



### 13.1 需要修改的文件



#### 文件 1：`src/weclaw/capabilities/tools.py`



**修改函数**：`_handle_bash`（第 339-367 行）



| 修改位置 | 当前行为 | 修改后行为 |

|----------|----------|------------|

| **第 350 行** shell 选择 | 硬编码 `["powershell", "-NoProfile", "-Command", command]` | 按 `os.name` 分支：Unix → `shutil.which("bash")` 动态查找，执行 `[bash_path, "-lc", command]`；Windows → 保持 PowerShell |

| **第 348-359 行** 异常处理 | 仅捕获 `TimeoutExpired` | 增加 `except (FileNotFoundError, OSError)` 捕获，返回可读 `_error_result` |

| **第 362-367 行** 输出格式 | `退出码: X\nSTDOUT:\n...\nSTDERR:...` | 头部增加 `Shell: bash/powershell` 和 `Workdir: /path` 元数据行 |

| **截断告知** | 6000 字符截断无提示 | 截断时追加 "（输出已截断）" 提示 |

| **workdir 不存在** | 抛 `FileNotFoundError` 到上层 | 捕获并返回可读错误 |



**不需要改的**：函数签名、第 802 行 `ToolSpec` 注册代码、schema 定义、risk_level、confirmation 策略。



#### 文件 2：`src/weclaw/agents/claude.py`



**修改位置**：`build_system_prompt` 函数内联 rules 的规则 5（第 102-109 行）



| 当前内容 | 修改后内容 |

|----------|------------|

| 要求 agent 先判断 Windows/Linux 环境，不确定时执行 `$PSVersionTable` | 告知 agent 系统已按平台自动选择 shell，Linux/macOS 上直接写 Bash 命令，Windows 上写 PowerShell 命令 |



具体操作：

- 删除 "不确定环境时，先执行 `$PSVersionTable` 判断，有输出就是 Windows"

- 替换为 "当前系统已按操作系统自动选择 shell，在 Linux/macOS 上直接编写 Bash 命令，在 Windows 上编写 PowerShell 命令"

- 保留 Windows 下应使用的 PowerShell 等效命令参考列表（仍具参考价值）



#### 文件 3：`src/weclaw/agents/openai.py`



**修改位置**：`build_openai_instructions` 函数中规则 9（第 172 行）



与 `claude.py` 相同的修正逻辑：删除 `$PSVersionTable` 判断指令，改为平台自适应引导。



### 13.2 修改执行顺序



```

步骤 1：修改 tools.py:_handle_bash              ← 核心执行器，唯一的功能改动

         ↓

步骤 2：修改 claude.py:build_system_prompt       ← agent 指令与执行器对齐

         ↓

步骤 3：修改 openai.py:build_openai_instructions ← 与 claude.py 保持一致

```



步骤 2 和 3 必须紧跟步骤 1，在**同一 commit 中提交**，否则会出现执行器与 prompt 认知反向漂移。



**附加操作**：如果用户存在 `workspace/prompts/rules.md` 文件，提醒其检查并更新其中的 PowerShell-first 指令。



### 13.3 影响性分析



| 影响范围 | 当前状态 | 修改后 | 风险等级 |

|----------|----------|--------|----------|

| 工具名 | `bash` | 不变 | 无 |

| 工具 schema | `command`, `workdir`, `timeout` | 不变 | 无 |

| ToolSpec 注册（第 802 行） | 不变 | 不变 | 无 |

| MCP 定义 `build_mcp_parameters()` | 不变 | 不变 | 无 |

| OpenAI function calling 定义 | 不变 | 不变 | 无 |

| Workflow 调用 bash | 直接调用 | 不变 | 无 |

| 确认策略 | `mode="always"` | 不变 | 无 |

| Provider allowlists | 无白名单过滤 | 不变 | 无 |

| Linux/macOS 执行 | PowerShell（失败） | Bash | **修复** |

| Windows 执行 | PowerShell | PowerShell | 不变 |

| 输出格式 | 无 shell 元数据 | 增加 `Shell:` / `Workdir:` 头 | 低 |

| Agent prompt | PowerShell-first | 平台自适应 | 中：需同步更新 |

| 已有 skill 文件 | 不涉及 | 不涉及 | 无 |

| 已有 workflow JSON | 不涉及 | 不涉及 | 无 |

| 外部 rules.md | 可能含旧指令 | 需用户手动检查 | 低：提醒即可 |

| Dashboard 解析 | 可能依赖 `退出码:` 格式 | 格式变更 | 待确认 |



---



## 14. 测试方案与用例



### 14.1 测试矩阵



| 平台 | 场景 | 说明 |

|------|------|------|

| Linux（当前环境） | 全部用例 | 主运行平台 |

| Windows | 关键用例 | 回归验证（通过 mock 验证分支逻辑） |



### 14.2 修改点 1：平台调度（`tools.py:_handle_bash`）



#### TC-1.1：Linux 上执行基础 Bash 命令



- **目标**：验证 Linux 平台走 Bash 而非 PowerShell

- **前置**：Linux 环境，`/bin/bash` 存在

- **输入**：`{"command": "echo hello", "timeout": "10"}`

- **通过标准**：

  1. 执行成功，退出码为 0

  2. STDOUT 中包含 `hello`

  3. 输出文本中包含 `Shell: bash`

  4. 不依赖 powershell 可执行文件



#### TC-1.2：Linux 上执行 Bash 特有语法



- **目标**：验证真正使用 Bash 而非 PowerShell

- **输入**：`{"command": "echo $HOME | grep /home"}`

- **通过标准**：

  1. 执行成功（`$HOME` 展开、管道符 `|`、`grep` 等 Unix 语法生效）

  2. 输出中包含 `Shell: bash`

  3. 输出中包含用户 home 路径



#### TC-1.3：Linux 上 Bash 登录 shell 特性



- **目标**：`-lc` 参数使登录 shell 加载 `.bashrc`/`.bash_profile`

- **输入**：`{"command": "echo $PATH"}`

- **通过标准**：

  1. 执行成功

  2. 输出的 PATH 包含用户 shell 配置的路径（证明 `-l` 生效）

  3. 执行 `pwd` 验证实际工作目录与 `workdir` 参数一致



#### TC-1.4：Windows 路径分支（mock 验证）



- **目标**：验证 `os.name == "nt"` 分支仍走 PowerShell

- **方法**：mock `os.name = "nt"`，注入假的 `subprocess.run`，检查传入参数

- **输入**：`{"command": "Get-ChildItem", "timeout": "10"}`

- **通过标准**：

  1. `subprocess.run` 被调用时第一个参数为 `["powershell", "-NoProfile", "-Command", "Get-ChildItem"]`

  2. 输出文本中包含 `Shell: powershell`



#### TC-1.5：`shutil.which("bash")` 找不到时返回明确错误



- **目标**：验证 shell 二进制不可用时不抛异常

- **方法**：mock `subprocess.run` 抛出 `FileNotFoundError`

- **通过标准**：

  1. 返回 `ToolResult`，`is_error=True`

  2. `to_text()` 包含可读错误信息（如 "找不到 shell 可执行文件"）

  3. 不抛出 Python 异常到上层



### 14.3 修改点 2：异常处理补全（`tools.py:_handle_bash`）



#### TC-2.1：空命令（回归）



- **目标**：与修改前行为一致

- **输入**：`{"command": ""}`

- **通过标准**：

  1. 返回 `is_error=True`

  2. 文本包含 "command 不能为空"



#### TC-2.2：超时处理（回归）



- **目标**：验证超时处理逻辑不被改动破坏

- **输入**：`{"command": "sleep 999", "timeout": "2"}`

- **通过标准**：

  1. 返回 `is_error=True`

  2. 文本包含 "超时" 字样

  3. 在 2-3 秒内返回



#### TC-2.3：workdir 不存在时返回明确错误



- **目标**：验证 `cwd` 路径不存在时不抛异常

- **输入**：`{"command": "echo test", "workdir": "nonexistent_dir"}`

- **通过标准**：

  1. 返回 `is_error=True`

  2. 文本包含可读错误信息

  3. 不抛出 Python 异常



#### TC-2.4：OSError 类异常被捕获



- **目标**：覆盖 `FileNotFoundError` 之外的其他 OS 异常

- **方法**：mock `subprocess.run` 抛出 `PermissionError`

- **通过标准**：

  1. 返回 `is_error=True`

  2. 文本包含可读错误信息



### 14.4 修改点 3：输出格式（`tools.py:_handle_bash`）



#### TC-3.1：成功执行包含 shell 元数据



- **输入**：`{"command": "echo test"}`

- **通过标准**：

  1. 输出包含 `Shell: bash`

  2. 输出包含 `Workdir:` 后跟有效路径

  3. 输出包含 `Exit code: 0`

  4. 输出包含 `STDOUT:` 段，其中可见 `test`



#### TC-3.2：错误执行包含 shell 元数据



- **输入**：`{"command": "exit 1"}`

- **通过标准**：

  1. 输出包含 `Exit code: 1`（或相应非零退出码）

  2. 输出包含 `Shell: bash`



#### TC-3.3：stderr 非空时展示 STDERR 段



- **输入**：`{"command": "echo error >&2 && echo ok"}`

- **通过标准**：

  1. 输出同时包含 `STDOUT:` 和 `STDERR:` 段

  2. STDERR 段中包含 `error`

  3. STDOUT 段中包含 `ok`



#### TC-3.4：大输出截断时有告知



- **目标**：修改前截断无告知，修改后应告知

- **输入**：`{"command": "python -c \"print('a' * 12000)\""}`

- **通过标准**：

  1. STDOUT 段内容不超过 6000 字符

  2. 输出末尾包含截断提示（如 "（输出已截断）"）



### 14.5 修改点 4：Claude 系统提示对齐（`agents/claude.py`）



#### TC-4.1：Prompt 不再要求 `$PSVersionTable` 判断



- **目标**：验证 rules 中不再包含 PowerShell-first 环境判断

- **方法**：调用 `build_system_prompt("test")`，检查返回字符串

- **通过标准**：

  1. 返回字符串中**不包含** `$PSVersionTable`

  2. 返回字符串中**不包含** "不确定环境时先执行 `$PSVersionTable`" 的环境判断指令

  3. **保留** Windows PowerShell 等效命令的速查表（Move-Item、Remove-Item 等）

  4. 包含平台自适应引导描述

  5. 仍保留 Bash 工具使用场景的规则（多步骤文件操作等）

  6. 其他规则内容保持不变



#### TC-4.2：外部 prompt 片段文件优先（回归）



- **目标**：验证 `workspace/prompts/rules.md` 存在时优先使用文件内容

- **方法**：在 `workspace/prompts/` 下创建 `rules.md`，调用 `build_system_prompt`

- **通过标准**：

  1. 返回的 prompt 包含 `rules.md` 的内容

  2. 不以内联规则替代



### 14.6 修改点 5：OpenAI 系统提示对齐（`agents/openai.py`）



#### TC-5.1：Prompt 不再要求 `$PSVersionTable` 判断



- **目标**：验证 OpenAI agent 的系统提示与 Claude 保持一致

- **方法**：调用 `build_openai_instructions("test")`，检查返回字符串

- **通过标准**：

  1. 返回字符串中**不包含** `$PSVersionTable`

  2. 返回字符串中**不包含** "不确定环境时先执行 `$PSVersionTable`"

  3. 包含平台自适应引导

  4. 其他规则 1-8 保持不变



#### TC-5.2：与 Claude prompt 语义一致



- **目标**：确保两个 agent 对 bash 工具的描述不会产生矛盾

- **方法**：分别获取两个 prompt 中关于 bash/shell 的描述段

- **通过标准**：

  1. 两个 prompt 中关于 shell 平台行为的核心描述一致

  2. 不会出现一个说用 Bash、另一个说用 PowerShell 的矛盾



### 14.7 回归测试：不受影响的模块



#### TC-R1：非 bash 工具不受影响



- **目标**：验证工具注册和调用对非 bash 工具无变化

- **方法**：依次调用 `get_current_time`、`list_workspace_files`

- **通过标准**：

  1. 每个工具正常返回结果

  2. `ToolSpec` 定义与修改前完全一致



#### TC-R2：工作流调用兼容（回归）



- **目标**：验证 workflow 引擎对工具的调用路径不变

- **方法**：执行内置 workflow `workflow_create_skill_with_preview`

- **通过标准**：

  1. 工作流正常执行完成

  2. 无异常



#### TC-R3：确认策略不变（回归）



- **目标**：验证 bash 工具的确认策略未被改动

- **方法**：获取 `get_tool_spec("bash")`，检查 `confirmation` 字段

- **通过标准**：

  1. `spec.requires_confirmation()` 返回 `True`

  2. `spec.confirmation.mode == "always"`

  3. `prompt_template` 与修改前一致



#### TC-R4：工具目录展示兼容（回归）



- **目标**：验证 `catalog.py` 展示 bash 工具信息正常

- **方法**：调用 `build_tool_detail_text("bash")`

- **通过标准**：

  1. 返回非 None 字符串

  2. 包含工具名、类别、风险级别、参数列表

  3. 不包含格式错误或 traceback



#### TC-R5：OpenAI tool definitions 兼容（回归）



- **目标**：验证 `build_openai_definition()` 对 bash 工具的投影不变

- **方法**：获取 `get_tool_spec("bash").build_openai_definition()`

- **通过标准**：

  1. 返回合法 JSON 结构

  2. `function.name == "bash"`

  3. `function.parameters` 包含 `command`、`workdir`、`timeout`

  4. `required` 列表包含 `command`



#### TC-R6：Claude MCP tool 兼容（回归）



- **目标**：验证 `build_mcp_parameters()` 对 bash 工具的参数类型映射不变

- **方法**：获取 `get_tool_spec("bash").build_mcp_parameters()`

- **通过标准**：

  1. 返回 `{"command": str, "workdir": str, "timeout": int}`

  2. 与修改前完全一致



#### TC-R7：Dashboard 输出格式兼容（回归）



- **目标**：验证 dashboard 工具追踪面板不因输出格式变化而解析失败

- **方法**：执行一次 `bash` 工具，检查 dashboard 是否展示正常

- **通过标准**：

  1. dashboard 工具追踪面板正常展示

  2. 无格式错误或空白字段



### 14.8 测试用例汇总表



| 用例 ID | 修改点 | 类型 | 优先级 | 运行方式 |

|---------|--------|------|--------|----------|

| TC-1.1 | 平台调度 | 正向 | P0 | Linux 直跑 |

| TC-1.2 | 平台调度 | 正向 | P0 | Linux 直跑 |

| TC-1.3 | 平台调度 | 正向 | P1 | Linux 直跑 |

| TC-1.4 | 平台调度 | 分支 | P0 | mock `os.name` |

| TC-1.5 | 平台调度 | 异常 | P1 | mock `FileNotFoundError` |

| TC-2.1 | 异常处理 | 回归 | P0 | Linux 直跑 |

| TC-2.2 | 异常处理 | 回归 | P0 | Linux 直跑 |

| TC-2.3 | 异常处理 | 新增 | P1 | Linux 直跑 |

| TC-2.4 | 异常处理 | 新增 | P1 | mock `PermissionError` |

| TC-3.1 | 输出格式 | 正向 | P0 | Linux 直跑 |

| TC-3.2 | 输出格式 | 正向 | P0 | Linux 直跑 |

| TC-3.3 | 输出格式 | 正向 | P1 | Linux 直跑 |

| TC-3.4 | 输出格式 | 新增 | P1 | Linux 直跑 |

| TC-4.1 | Claude prompt | 正向 | P0 | 纯 Python 调用 |

| TC-4.2 | Claude prompt | 回归 | P1 | 需准备 rules.md |

| TC-5.1 | OpenAI prompt | 正向 | P0 | 纯 Python 调用 |

| TC-5.2 | Prompt 一致性 | 正向 | P1 | 纯 Python 调用 |

| TC-R1 | 工具注册 | 回归 | P0 | Linux 直跑 |

| TC-R2 | 工作流 | 回归 | P1 | Linux 直跑 |

| TC-R3 | 确认策略 | 回归 | P0 | Linux 直跑 |

| TC-R4 | 目录展示 | 回归 | P1 | 纯 Python 调用 |

| TC-R5 | OpenAI 投影 | 回归 | P0 | 纯 Python 调用 |

| TC-R6 | MCP 投影 | 回归 | P0 | 纯 Python 调用 |

| TC-R7 | Dashboard 兼容 | 回归 | P1 | 启动 dashboard 验证 |



**统计**：共 24 个用例。P0 用例 13 个（正向 7 个 + 回归 6 个），P1 用例 11 个。其中 17 个可在当前 Linux 环境直接运行，7 个需通过 mock 模拟 Windows/异常路径或依赖外部系统。
