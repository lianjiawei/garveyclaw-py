from __future__ import annotations



import base64
import html
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field

from datetime import datetime

from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import quote_plus

import httpx

import weclaw.config as config
from weclaw.core.confirmation import ToolConfirmationRequest, has_session_tool_grant, request_tool_confirmation

from weclaw.core.agent_activity import mark_agent_tool_cancelled, mark_agent_tool_started, mark_agent_waiting

from weclaw.core.delivery import MessageSender, send_sender_file, send_sender_text

from weclaw.decision.trace import record_tool_trace_finish, record_tool_trace_start

from weclaw.core.types import ConversationRef

from weclaw.cluster.store import mark_cluster_agent_waiting, mark_cluster_task_finished, mark_cluster_task_started

from weclaw.tasks.repository import cancel_scheduled_task_record, list_scheduled_task_records

from weclaw.tasks.service import create_scheduled_task



ToolHandler = Callable[[dict[str, Any], "ToolContext"], Awaitable["ToolResult"]]

ToolSummaryBuilder = Callable[[dict[str, Any]], str]

ToolAvailability = Callable[["ToolContext"], bool]



_MCP_TYPE_MAP: dict[str, type[Any]] = {

    "string": str,

    "integer": int,

    "boolean": bool,

    "number": float,

}



_AUTO_APPROVED_CURRENT_CONVERSATION_TOOLS = frozenset({"create_task", "send_message"})





@dataclass(slots=True)

class ToolContext:

    sender: MessageSender | None

    target_id: str | int

    uploaded_image: Any | None = None

    channel: str | None = None

    session_scope: str | None = None

    enforce_confirmations: bool = False



    @property

    def conversation(self) -> ConversationRef | None:

        if not self.channel or not self.session_scope:

            return None

        return ConversationRef(channel=self.channel, target_id=str(self.target_id), session_scope=self.session_scope)





@dataclass(slots=True)

class ToolResult:

    content: list[dict[str, Any]]

    is_error: bool = False



    def to_mcp_payload(self) -> dict[str, Any]:

        payload: dict[str, Any] = {"content": self.content}

        if self.is_error:

            payload["is_error"] = True

        return payload



    def to_text(self) -> str:

        parts: list[str] = []

        for block in self.content:

            block_type = block.get("type")

            if block_type == "text":

                parts.append(str(block.get("text") or ""))

            elif block_type == "image":

                parts.append("[image]")

        return "\n".join(part for part in parts if part).strip()





@dataclass(frozen=True, slots=True)

class ConfirmationPolicy:

    mode: str = "never"

    prompt_template: str | None = None



    @property

    def requires_confirmation(self) -> bool:

        return self.mode != "never"



    def build_prompt(self, tool_name: str, summary: str) -> str | None:

        if not self.requires_confirmation:

            return None

        if self.prompt_template:

            return self.prompt_template.format(tool_name=tool_name, summary=summary)

        if summary:

            return f"请确认是否执行工具 `{tool_name}`：{summary}"

        return f"请确认是否执行工具 `{tool_name}`。"





@dataclass(slots=True)

class ToolSpec:

    name: str

    description: str

    parameters: dict[str, Any]

    handler: ToolHandler

    providers: frozenset[str] = field(default_factory=lambda: frozenset({"claude", "openai"}))

    summary_builder: ToolSummaryBuilder = field(default=lambda _args: "")

    availability: ToolAvailability | None = None

    risk_level: str = "normal"

    category: str = "general"

    confirmation: ConfirmationPolicy = field(default_factory=ConfirmationPolicy)

    source_path: Path | None = None



    def supports(self, provider: str, context: ToolContext | None = None) -> bool:

        if provider not in self.providers:

            return False

        if self.availability is not None and context is not None:

            return self.availability(context)

        return True



    def build_openai_definition(self) -> dict[str, Any]:

        return {

            "type": "function",

            "function": {

                "name": self.name,

                "description": self.description,

                "parameters": {

                    "type": "object",

                    "properties": self.parameters.get("properties", {}),

                    "required": list(self.parameters.get("required", [])),

                },

            },

        }



    def build_mcp_parameters(self) -> dict[str, type[Any]]:

        properties = self.parameters.get("properties", {})

        return {

            name: _MCP_TYPE_MAP.get(str(meta.get("type") or "string"), str)

            for name, meta in properties.items()

        }



    def build_summary(self, arguments: dict[str, Any]) -> str:

        return self.summary_builder(arguments)



    def requires_confirmation(self) -> bool:

        if self.name in _AUTO_APPROVED_CURRENT_CONVERSATION_TOOLS:

            return False

        return self.confirmation.requires_confirmation



    def build_confirmation_prompt(self, arguments: dict[str, Any]) -> str | None:

        return self.confirmation.build_prompt(self.name, self.build_summary(arguments))





class ToolRegistry:

    def __init__(self) -> None:

        self._specs: list[ToolSpec] = []

        self._by_name: dict[str, ToolSpec] = {}



    def register(self, spec: ToolSpec) -> None:

        if spec.name in self._by_name:

            raise ValueError(f"Duplicate tool name: {spec.name}")

        self._specs.append(spec)

        self._by_name[spec.name] = spec



    def get(self, name: str) -> ToolSpec | None:

        return self._by_name.get(name)



    def list(self, provider: str | None = None, context: ToolContext | None = None) -> list[ToolSpec]:

        specs = self._specs

        if provider is None:

            return list(specs)

        return [spec for spec in specs if spec.supports(provider, context)]





@dataclass(frozen=True, slots=True)

class ToolRegistryStatus:

    loaded_at: datetime

    workflow_directory: Path

    workflow_snapshot_entries: tuple[tuple[str, int, int], ...]

    loaded_workflow_files: tuple[str, ...]

    last_error: str | None = None

    failed_at: datetime | None = None



    @property

    def state(self) -> str:

        return "error" if self.last_error else "ok"





def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:

    return {

        "type": "object",

        "properties": properties,

        "required": required or [],

    }





def _text_result(text: str) -> ToolResult:

    return ToolResult(content=[{"type": "text", "text": text}])





def _error_result(text: str) -> ToolResult:

    return ToolResult(content=[{"type": "text", "text": text}], is_error=True)





def _resolve_workspace_path(relative_path: str) -> Path:

    candidate = (config.WORKSPACE_DIR / relative_path).resolve()

    workspace_root = config.WORKSPACE_DIR.resolve()

    if candidate != workspace_root and workspace_root not in candidate.parents:

        raise ValueError("Path is outside the allowed workspace.")

    return candidate





def _resolve_workdir(relative_path: str | None) -> Path:

    if not relative_path:

        return config.WORKSPACE_DIR

    return _resolve_workspace_path(relative_path)





def _parse_tool_datetime(value: str) -> datetime:

    text = value.strip()

    try:

        parsed = datetime.fromisoformat(text)

    except ValueError:

        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")

    if parsed.tzinfo is None:

        return parsed.astimezone()

    return parsed





def _build_task_display_text(prompt: str) -> str:

    normalized = prompt.strip()

    if "任务内容：" in normalized:

        normalized = normalized.split("任务内容：", maxsplit=1)[-1].strip()

    return normalized or prompt.strip()





def _tool_name_summary(key: str) -> ToolSummaryBuilder:

    def _builder(arguments: dict[str, Any]) -> str:

        return str(arguments.get(key) or "")



    return _builder





def _tool_command_summary(arguments: dict[str, Any]) -> str:

    return str(arguments.get("command") or "")[:120]





def _uploaded_image_available(ctx: ToolContext) -> bool:

    return ctx.uploaded_image is not None





def _confirm(prompt_template: str | None = None) -> ConfirmationPolicy:

    return ConfirmationPolicy(mode="always", prompt_template=prompt_template)





async def _handle_get_current_time(_args: dict[str, Any], _ctx: ToolContext) -> ToolResult:

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return _text_result(f"Current local time is: {now}")





async def _handle_list_workspace_files(_args: dict[str, Any], _ctx: ToolContext) -> ToolResult:

    items = sorted(path.name for path in config.WORKSPACE_DIR.iterdir())

    text = "\n".join(f"- {name}" for name in items) if items else "(workspace is empty)"

    return _text_result(f"Workspace directory: {config.WORKSPACE_DIR}\n{text}")





async def _handle_read_workspace_file(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:

    relative_path = str(args.get("path") or "").strip()

    if not relative_path:

        return _error_result("错误：path 不能为空。")

    try:

        target = _resolve_workspace_path(relative_path)

    except ValueError as exc:

        return _error_result(str(exc))

    if not target.exists():

        return _error_result(f"File not found: {relative_path}")

    if not target.is_file():

        return _error_result(f"Not a file: {relative_path}")

    content = target.read_text(encoding="utf-8", errors="replace")

    return _text_result(f"File: {relative_path}\n\n{content}")





async def _handle_write_workspace_file(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:

    relative_path = str(args.get("path") or "").strip()

    content = str(args.get("content") or "")

    if not relative_path:

        return _error_result("错误：path 不能为空。")

    try:

        target = _resolve_workspace_path(relative_path)

    except ValueError as exc:

        return _error_result(f"错误：{exc}")

    target.parent.mkdir(parents=True, exist_ok=True)

    target.write_text(content, encoding="utf-8")

    return _text_result(f"文件已写入：{relative_path}")





async def _handle_edit_workspace_file(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:

    relative_path = str(args.get("path") or "").strip()

    old_string = str(args.get("old_string") or "")

    new_string = str(args.get("new_string") or "")

    replace_all = bool(args.get("replace_all", False))

    if not relative_path or not old_string:

        return _error_result("错误：path 和 old_string 不能为空。")

    try:

        target = _resolve_workspace_path(relative_path)

    except ValueError as exc:

        return _error_result(f"错误：{exc}")

    if not target.exists() or not target.is_file():

        return _error_result(f"文件不存在：{relative_path}")

    content = target.read_text(encoding="utf-8", errors="replace")

    if old_string not in content:

        return _error_result("错误：未找到要替换的文本。")

    updated = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)

    target.write_text(updated, encoding="utf-8")

    return _text_result(f"文件已更新：{relative_path}")





async def _handle_glob_workspace_files(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:

    pattern = str(args.get("pattern") or "").strip()

    if not pattern:

        return _error_result("错误：pattern 不能为空。")

    matches = sorted({str(path.relative_to(config.WORKSPACE_DIR)) for path in config.WORKSPACE_DIR.glob(pattern)})

    return _text_result("没有匹配文件。" if not matches else "\n".join(matches[:200]))





async def _handle_grep_workspace_content(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:

    pattern = str(args.get("pattern") or "").strip()

    include = str(args.get("include") or "**/*").strip() or "**/*"

    if not pattern:

        return _error_result("错误：pattern 不能为空。")

    try:

        regex = re.compile(pattern)

    except re.error as exc:

        return _error_result(f"错误：无效正则：{exc}")

    matches: list[str] = []

    for path in config.WORKSPACE_DIR.glob(include):

        if not path.is_file():

            continue

        try:

            text = path.read_text(encoding="utf-8", errors="replace")

        except OSError:

            continue

        for line_no, line in enumerate(text.splitlines(), 1):

            if regex.search(line):

                matches.append(f"{path.relative_to(config.WORKSPACE_DIR)}:{line_no}: {line[:300]}")

                if len(matches) >= 200:

                    return _text_result("\n".join(matches))

    return _text_result("没有匹配内容。" if not matches else "\n".join(matches))





async def _handle_bash(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:

    command = str(args.get("command") or "").strip()

    if not command:

        return _error_result("错误：command 不能为空。")

    timeout = int(args.get("timeout") or 60)

    try:

        workdir = _resolve_workdir(str(args.get("workdir") or "").strip() or None)

    except ValueError as exc:

        return _error_result(f"错误：{exc}")



    # 按宿主平台选择 shell

    if os.name == "nt":

        shell_cmd = ["powershell", "-NoProfile", "-Command", command]

        shell_name = "powershell"

    else:

        bash_path = shutil.which("bash")

        if bash_path is None:

            return _error_result("错误：此系统上找不到 bash 可执行文件。")

        shell_cmd = [bash_path, "-lc", command]

        shell_name = f"bash ({bash_path})"



    try:

        result = subprocess.run(

            shell_cmd,

            cwd=str(workdir),

            capture_output=True,

            text=True,

            timeout=timeout,

            encoding="utf-8",

            errors="replace",

        )

    except subprocess.TimeoutExpired:

        return _error_result(f"错误：命令执行超时（{timeout} 秒）。")

    except (FileNotFoundError, OSError) as exc:

        return _error_result(f"错误：shell 执行失败：{exc}")



    output = (result.stdout or "").strip()

    error = (result.stderr or "").strip()

    truncated_out = len(output) > 6000

    truncated_err = len(error) > 6000

    parts = [

        f"Shell: {shell_name}",

        f"Workdir: {workdir}",

        f"Exit code: {result.returncode}",

    ]

    if output:

        out_text = output[:6000]

        if truncated_out:

            out_text += "\n（输出已截断）"

        parts.append(f"STDOUT:\n{out_text}")

    if error:

        err_text = error[:6000]

        if truncated_err:

            err_text += "\n（输出已截断）"

        parts.append(f"STDERR:\n{err_text}")

    return _text_result("\n\n".join(parts))





async def _handle_web_search(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    query = str(args.get("query") or "").strip()
    if not query:
        return _error_result("错误：query 不能为空。")
    if not config.TAVILY_API_KEY:
        return await _handle_default_web_search(query)
    try:
        response = await _search_tavily_http(query)
    except Exception as exc:
        return await _handle_default_web_search(query, fallback_reason=f"Tavily 搜索失败，已自动回退。原因：{_format_search_error(exc)}")
    results = response.get("results", [])
    if not results:
        return await _handle_default_web_search(query, fallback_reason="Tavily 没有返回结果，已自动回退到系统默认轻量搜索。")
    lines: list[str] = []

    for index, result in enumerate(results, 1):

        title = result.get("title", "")

        url = result.get("url", "")

        content = (result.get("content", "") or "")[:300]

        lines.append(f"{index}. {title}\n   {url}\n   {content}")
    return _text_result(f"Search results for '{query}':\n\n" + "\n\n".join(lines))


async def _search_tavily_http(query: str) -> dict[str, Any]:
    payload = {
        "query": query,
        "search_depth": config.TAVILY_SEARCH_DEPTH,
        "max_results": config.TAVILY_MAX_RESULTS,
    }
    headers = {
        "Authorization": f"Bearer {config.TAVILY_API_KEY}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post("https://api.tavily.com/search", headers=headers, json=payload)
        response.raise_for_status()
        return response.json()


def _format_search_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        try:
            detail = response.json()
        except ValueError:
            detail = response.text[:200]
        return f"HTTP {response.status_code} {detail}"
    return f"{type(exc).__name__}: {str(exc)[:200]}"


def _clean_search_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


async def _handle_default_web_search(query: str, *, fallback_reason: str | None = None) -> ToolResult:
    search_url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            response = await client.get(search_url, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
    except Exception as exc:
        return _error_result(
            "默认搜索暂时不可用。你可以配置 TAVILY_API_KEY 获得更稳定的联网搜索能力。"
            f"\nDetails: {exc}"
        )

    matches = re.findall(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
        response.text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    lines = []
    if fallback_reason:
        lines.append(fallback_reason)
        lines.append("搜索来源：系统默认轻量搜索。")
        tip = "提示：Tavily 已配置但本次请求失败；默认搜索可能在结果质量、时效性和访问频率上受限制。"
    else:
        lines.append("搜索来源：系统默认轻量搜索（未配置 Tavily）。")
        tip = "提示：默认搜索可能在结果质量、时效性和访问频率上受限制；需要更稳定效果建议配置 TAVILY_API_KEY。"
    lines.extend(
        [
            tip,
            "",
        ]
    )
    max_results = max(1, min(config.TAVILY_MAX_RESULTS, 5))
    for index, (url, title, snippet) in enumerate(matches[:max_results], 1):
        lines.append(f"{index}. {_clean_search_text(title)}")
        lines.append(f"   URL: {html.unescape(url)}")
        clean_snippet = _clean_search_text(snippet)
        if clean_snippet:
            lines.append(f"   {clean_snippet}")
    if len(lines) <= 3:
        lines.append("默认搜索没有返回可用结果。")
    return _text_result("\n".join(lines))




async def _handle_send_message(args: dict[str, Any], ctx: ToolContext) -> ToolResult:

    if ctx.sender is None:

        return _error_result("Error: Current context cannot send messages.")

    text = str(args.get("text") or "").strip()

    if not text:

        return _error_result("错误：text 不能为空。")

    await send_sender_text(ctx.sender, ctx.target_id, text)

    return _text_result("Message sent to the current conversation successfully.")





async def _handle_send_file(args: dict[str, Any], ctx: ToolContext) -> ToolResult:

    if ctx.sender is None:

        return _error_result("Error: Current context cannot send files.")

    raw_path = str(args.get("path") or "").strip()

    if not raw_path:

        return _error_result("错误：path 参数不能为空。")

    file_path = Path(raw_path)

    if not file_path.is_absolute():

        file_path = config.WORKSPACE_DIR / file_path

    resolved = file_path.resolve()

    if not str(resolved).startswith(str(config.WORKSPACE_DIR.resolve())):

        return _error_result(f"Error: Path '{raw_path}' is outside the workspace.")

    if not resolved.is_file():

        return _error_result(f"Error: File not found: {raw_path}")

    try:

        file_data = resolved.read_bytes()

    except Exception as exc:

        return _error_result(f"Error reading file: {exc}")

    await send_sender_file(ctx.sender, ctx.target_id, file_data, resolved.name)

    return _text_result(f"File '{resolved.name}' has been sent to the current conversation.")





async def _handle_get_uploaded_image(_args: dict[str, Any], ctx: ToolContext) -> ToolResult:

    if ctx.uploaded_image is None:

        return _error_result("No image was uploaded in this turn.")

    return ToolResult(

        content=[

            {"type": "text", "text": "This is the image uploaded by the user in the current turn."},

            {

                "type": "image",

                "data": base64.b64encode(ctx.uploaded_image.data).decode("ascii"),

                "mimeType": ctx.uploaded_image.mime_type,

            },

        ]

    )





async def _handle_list_tasks(_args: dict[str, Any], ctx: ToolContext) -> ToolResult:

    if not ctx.channel:

        return _error_result("Error: Channel context is missing. Cannot list tasks.")

    try:

        tasks = await list_scheduled_task_records(channel=ctx.channel, target_id=str(ctx.target_id))

    except Exception as exc:

        return _error_result(f"Failed to list tasks: {exc}")

    if not tasks:

        return _text_result("No scheduled tasks found for this conversation.")

    lines = [f"Scheduled tasks for this conversation (channel: {ctx.channel}):"]

    for index, task in enumerate(tasks, 1):

        task_id = task.get("id", "unknown")

        prompt = _build_task_display_text(task.get("prompt", "unknown"))

        next_run = task.get("next_run", "unknown")

        schedule_type = task.get("schedule_type", "once")

        lines.append(f"{index}. {next_run} | {schedule_type} | {prompt}")

        lines.append(f"   Internal ID: {task_id}")

        lines.append("")

    return _text_result("\n".join(lines))





async def _handle_cancel_task(args: dict[str, Any], ctx: ToolContext) -> ToolResult:

    if not ctx.channel:

        return _error_result("Error: Channel context is missing. Cannot cancel task.")

    task_id = str(args.get("task_id") or "").strip()

    if not task_id:

        return _error_result("错误：task_id 不能为空。")

    try:

        success = await cancel_scheduled_task_record(task_id, channel=ctx.channel, target_id=str(ctx.target_id))

    except Exception as exc:

        return _error_result(f"Failed to cancel task: {exc}")

    if success:

        return _text_result(f"Task {task_id} has been cancelled successfully.")

    return _error_result(f"Task {task_id} not found or already cancelled.")





async def _handle_create_task(args: dict[str, Any], ctx: ToolContext) -> ToolResult:

    if not ctx.channel or not ctx.session_scope:

        return _error_result("Error: Conversation context is missing. Cannot create task.")

    prompt = str(args.get("prompt") or "").strip()

    if not prompt:

        return _error_result("Error: prompt cannot be empty.")

    try:

        run_at = _parse_tool_datetime(str(args.get("run_at") or ""))

    except ValueError as exc:

        return _error_result(f"Invalid run_at: {exc}")

    conversation = ConversationRef(channel=ctx.channel, target_id=str(ctx.target_id), session_scope=ctx.session_scope)

    task_id = await create_scheduled_task(conversation=conversation, prompt=prompt, run_at=run_at)

    return _text_result(f"Task created successfully. ID: {task_id}, run_at: {run_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')}, prompt: {prompt}")





async def _handle_list_skills(_args: dict[str, Any], _ctx: ToolContext) -> ToolResult:

    from weclaw.skills import store as skill_store



    skills = skill_store.list_skills()

    if not skills:

        return _text_result("当前没有可用的 skill。")

    lines = ["Available skills:"]

    for skill in skills:

        lines.append(f"- {skill.name}: {skill.description}")

    return _text_result("\n".join(lines))





async def _handle_read_skill(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:

    from weclaw.skills import store as skill_store



    skill_name = str(args.get("name") or "").strip()

    if not skill_name:

        return _error_result("错误：name 不能为空。")

    skill = skill_store.get_skill(skill_name)

    if skill is None:

        return _error_result(f"未找到名为 '{skill_name}' 的 skill。使用 list_skills 查看可用列表。")

    body = skill_store.get_body(skill)

    return _text_result(f"[Skill: {skill.name} | {skill.title}]\n{body}")





async def _handle_create_skill(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:

    from weclaw.skills import store as skill_store



    name = str(args.get("name") or "").strip()

    title = str(args.get("title") or "").strip()

    description = str(args.get("description") or "").strip()

    keywords = str(args.get("keywords") or "").strip()

    body = str(args.get("body") or "").strip()

    if not name:

        return _error_result("错误：name 不能为空。")

    if not re.match(r"^[a-z][a-z0-9_]*$", name):

        return _error_result("错误：name 只能包含小写字母、数字和下划线，且以小写字母开头。")

    if not description:

        return _error_result("错误：description 不能为空。")

    if not body:

        return _error_result("错误：body 不能为空。")

    target = skill_store.SKILLS_DIR / f"{name}_skill.md"

    if target.exists():

        return _error_result(f"错误：名为 '{name}' 的 skill 已存在（{target.name}）。")

    keywords_line = f"keywords: [{keywords}]" if keywords else "keywords: []"

    frontmatter = f"---\nname: {name}\ntitle: {title or name}\ndescription: {description}\n{keywords_line}\n---\n\n"

    try:

        target.write_text(frontmatter + body, encoding="utf-8")

    except Exception as exc:

        return _error_result(f"错误：写入文件失败：{exc}")

    return _text_result(f"Skill '{name}' 已创建，文件：{target.name}。下次调用时自动生效。")





async def _handle_update_skill(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:

    from weclaw.skills import store as skill_store



    name = str(args.get("name") or "").strip()

    if not name:

        return _error_result("错误：name 不能为空。")

    if not re.match(r"^[a-z][a-z0-9_]*$", name):

        return _error_result("错误：name 只能包含小写字母、数字和下划线。")

    skill = skill_store.get_skill(name)

    if skill is None:

        return _error_result(f"未找到名为 '{name}' 的 skill。使用 list_skills 查看可用列表。")

    target = skill.file_path

    if not target.exists():

        return _error_result(f"Skill '{name}' 的文件不存在。")

    try:

        raw = target.read_text(encoding="utf-8")

    except Exception as exc:

        return _error_result(f"错误：读取文件失败：{exc}")

    fm_match = re.match(r"^---\s*\n(.*?)\n---", raw, re.DOTALL)

    if not fm_match:

        return _error_result(f"错误：Skill '{name}' 文件格式无效（缺少 frontmatter）。")

    frontmatter_block = raw[:fm_match.end()]

    body_content = raw[fm_match.end():].strip()

    updated_fields: list[str] = []

    new_title = str(args.get("title") or "").strip()

    new_description = str(args.get("description") or "").strip()

    new_keywords = str(args.get("keywords") or "").strip()

    new_body = str(args.get("body") or "").strip()

    if new_title:

        frontmatter_block = re.sub(r"(?m)^title:.*$", f"title: {new_title}", frontmatter_block)

        updated_fields.append("title")

    if new_description:

        frontmatter_block = re.sub(r"(?m)^description:.*$", f"description: {new_description}", frontmatter_block)

        updated_fields.append("description")

    if new_keywords:

        frontmatter_block = re.sub(r"(?m)^keywords:.*$", f"keywords: [{new_keywords}]", frontmatter_block)

        updated_fields.append("keywords")

    if new_body:

        body_content = new_body

        updated_fields.append("body")

    if not updated_fields:

        return _error_result("错误：没有指定要更新的字段。传入 title/description/keywords/body 中的至少一个。")

    try:

        target.write_text(frontmatter_block + "\n" + body_content + "\n", encoding="utf-8")

    except Exception as exc:

        return _error_result(f"错误：写入文件失败：{exc}")

    return _text_result(f"Skill '{name}' 已更新字段：{', '.join(updated_fields)}。下次调用时自动生效。")





async def _handle_delete_skill(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:

    from weclaw.skills import store as skill_store



    name = str(args.get("name") or "").strip()

    if not name:

        return _error_result("错误：name 不能为空。")

    skill = skill_store.get_skill(name)

    if skill is None:

        return _error_result(f"未找到名为 '{name}' 的 skill。使用 list_skills 查看可用列表。")

    target = skill.file_path

    if not target.exists():

        return _error_result(f"Skill '{name}' 的文件不存在。")

    try:

        target.unlink()

    except Exception as exc:

        return _error_result(f"错误：删除文件失败：{exc}")

    return _text_result(f"Skill '{name}' 已删除（{target.name}）。下次调用时自动生效。")





async def _handle_list_workflows(_args: dict[str, Any], _ctx: ToolContext) -> ToolResult:

    from weclaw.capabilities import workflows as workflow_store



    try:

        workflows = workflow_store.list_workflows()

    except Exception as exc:

        return _error_result(f"错误：读取 workflow 列表失败：{exc}")

    if not workflows:

        return _text_result("当前没有可用的 workflow。")

    lines = ["Available workflows:"]

    for workflow in workflows:

        source = workflow.source_path.name if workflow.source_path is not None else "runtime"

        lines.append(f"- {workflow.name}: {workflow.description} ({source})")

    return _text_result("\n".join(lines))





async def _handle_read_workflow(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:

    from weclaw.capabilities import workflows as workflow_store



    name = str(args.get("name") or "").strip()

    if not name:

        return _error_result("错误：name 不能为空。")

    try:

        workflow = workflow_store.get_workflow(name)

    except workflow_store.WorkflowDefinitionError as exc:

        return _error_result(f"错误：{exc}")

    except Exception as exc:

        return _error_result(f"错误：读取 workflow 失败：{exc}")

    if workflow is None:

        return _error_result(f"未找到名为 '{name}' 的 workflow。使用 list_workflows 查看可用列表。")

    try:

        raw = workflow_store.read_workflow_definition(workflow.name)

    except FileNotFoundError:

        return _error_result(f"Workflow '{workflow.name}' 的定义文件不存在。")

    return _text_result(f"[Workflow: {workflow.name}]\n{raw}")





async def _handle_create_workflow(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:

    from weclaw.capabilities import workflows as workflow_store



    definition_json = str(args.get("definition_json") or "").strip()

    if not definition_json:

        return _error_result("错误：definition_json 不能为空。")

    try:

        spec = workflow_store.write_workflow_definition_text(definition_json, allow_overwrite=False)

        rebuild_tool_registry()

    except workflow_store.WorkflowDefinitionError as exc:

        return _error_result(f"错误：{exc}")

    except Exception as exc:

        return _error_result(f"错误：创建 workflow 失败：{exc}")

    return _text_result(f"Workflow '{spec.name}' 已创建，文件：{spec.source_path.name if spec.source_path else spec.name}。已自动热加载。")





async def _handle_update_workflow(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:

    from weclaw.capabilities import workflows as workflow_store



    name = str(args.get("name") or "").strip()

    definition_json = str(args.get("definition_json") or "").strip()

    if not name:

        return _error_result("错误：name 不能为空。")

    if not definition_json:

        return _error_result("错误：definition_json 不能为空。")

    try:

        existing = workflow_store.get_workflow(name)

    except workflow_store.WorkflowDefinitionError as exc:

        return _error_result(f"错误：{exc}")

    except Exception as exc:

        return _error_result(f"错误：读取 workflow 失败：{exc}")

    if existing is None:

        return _error_result(f"未找到名为 '{name}' 的 workflow。使用 list_workflows 查看可用列表。")

    try:

        spec = workflow_store.write_workflow_definition_text(definition_json, allow_overwrite=True)

        if spec.name != existing.name:

            return _error_result("错误：更新 workflow 时 definition_json 中的 name 不能改变。")

        rebuild_tool_registry()

    except workflow_store.WorkflowDefinitionError as exc:

        return _error_result(f"错误：{exc}")

    except Exception as exc:

        return _error_result(f"错误：更新 workflow 失败：{exc}")

    return _text_result(f"Workflow '{spec.name}' 已更新。已自动热加载。")





async def _handle_delete_workflow(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:

    from weclaw.capabilities import workflows as workflow_store



    name = str(args.get("name") or "").strip()

    if not name:

        return _error_result("错误：name 不能为空。")

    try:

        deleted_path = workflow_store.delete_workflow_definition(name)

        rebuild_tool_registry()

    except workflow_store.WorkflowDefinitionError as exc:

        return _error_result(f"错误：{exc}")

    except FileNotFoundError:

        return _error_result(f"未找到名为 '{name}' 的 workflow。使用 list_workflows 查看可用列表。")

    except Exception as exc:

        return _error_result(f"错误：删除 workflow 失败：{exc}")

    return _text_result(f"Workflow '{name}' 已删除（{deleted_path.name}）。已自动热加载。")





async def _handle_draft_workflow_from_request(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:

    from weclaw.capabilities import workflows as workflow_store



    name = str(args.get("name") or "").strip()

    request_text = str(args.get("request_text") or "").strip()

    description = str(args.get("description") or "").strip() or None

    if not name:

        return _error_result("错误：name 不能为空。")

    if not request_text:

        return _error_result("错误：request_text 不能为空。")

    try:

        definition = workflow_store.compile_workflow_definition_from_request(

            name=name,

            request_text=request_text,

            description=description,

        )

    except workflow_store.WorkflowDefinitionError as exc:

        return _error_result(f"错误：{exc}")

    return _text_result(definition)





async def _handle_create_workflow_from_request(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:

    from weclaw.capabilities import workflows as workflow_store



    name = str(args.get("name") or "").strip()

    request_text = str(args.get("request_text") or "").strip()

    description = str(args.get("description") or "").strip() or None

    if not name:

        return _error_result("错误：name 不能为空。")

    if not request_text:

        return _error_result("错误：request_text 不能为空。")

    try:

        definition = workflow_store.compile_workflow_definition_from_request(

            name=name,

            request_text=request_text,

            description=description,

        )

        spec = workflow_store.write_workflow_definition_text(definition, allow_overwrite=False)

        rebuild_tool_registry()

    except workflow_store.WorkflowDefinitionError as exc:

        return _error_result(f"错误：{exc}")

    except Exception as exc:

        return _error_result(f"错误：创建 workflow 失败：{exc}")

    return _text_result(f"Workflow '{spec.name}' 已根据自然语言请求创建，文件：{spec.source_path.name if spec.source_path else spec.name}。已自动热加载。")





async def _handle_update_workflow_from_request(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:

    from weclaw.capabilities import workflows as workflow_store



    name = str(args.get("name") or "").strip()

    request_text = str(args.get("request_text") or "").strip()

    description = str(args.get("description") or "").strip() or None

    if not name:

        return _error_result("错误：name 不能为空。")

    if not request_text:

        return _error_result("错误：request_text 不能为空。")

    try:

        existing = workflow_store.get_workflow(name)

    except workflow_store.WorkflowDefinitionError as exc:

        return _error_result(f"错误：{exc}")

    except Exception as exc:

        return _error_result(f"错误：读取 workflow 失败：{exc}")

    if existing is None:

        return _error_result(f"未找到名为 '{name}' 的 workflow。使用 list_workflows 查看可用列表。")

    try:

        definition = workflow_store.compile_workflow_definition_from_request(

            name=name,

            request_text=request_text,

            description=description,

            providers=sorted(existing.providers),

            risk_level=existing.risk_level,

        )

        spec = workflow_store.write_workflow_definition_text(definition, allow_overwrite=True)

        rebuild_tool_registry()

    except workflow_store.WorkflowDefinitionError as exc:

        return _error_result(f"错误：{exc}")

    except Exception as exc:

        return _error_result(f"错误：更新 workflow 失败：{exc}")

    return _text_result(f"Workflow '{spec.name}' 已根据自然语言请求更新。已自动热加载。")





def _build_default_registry() -> ToolRegistry:

    registry = ToolRegistry()

    registry.register(ToolSpec("get_current_time", "获取当前服务器本地时间。", _schema({}), _handle_get_current_time, category="system"))

    registry.register(ToolSpec("list_workspace_files", "列出工作区中的文件和目录。", _schema({}), _handle_list_workspace_files, category="workspace"))

    registry.register(ToolSpec("read_workspace_file", "读取工作区中的文本文件。", _schema({"path": {"type": "string", "description": "工作区内的相对文件路径"}}, ["path"]), _handle_read_workspace_file, summary_builder=_tool_name_summary("path"), category="workspace"))

    registry.register(ToolSpec("write_workspace_file", "在工作区中写入文本文件，不存在则创建。", _schema({"path": {"type": "string", "description": "工作区内的相对文件路径"}, "content": {"type": "string", "description": "要写入的完整文本内容"}}, ["path", "content"]), _handle_write_workspace_file, providers=frozenset({"openai", "claude"}), summary_builder=_tool_name_summary("path"), risk_level="write", category="workspace", confirmation=_confirm("请确认是否写入文件：{summary}")))

    registry.register(ToolSpec("edit_workspace_file", "在工作区文本文件里替换指定字符串。", _schema({"path": {"type": "string", "description": "工作区内的相对文件路径"}, "old_string": {"type": "string", "description": "要替换的原始字符串"}, "new_string": {"type": "string", "description": "新的字符串"}, "replace_all": {"type": "boolean", "description": "是否替换全部匹配，默认 false"}}, ["path", "old_string", "new_string"]), _handle_edit_workspace_file, providers=frozenset({"openai", "claude"}), summary_builder=_tool_name_summary("path"), risk_level="write", category="workspace", confirmation=_confirm("请确认是否修改文件：{summary}")))

    registry.register(ToolSpec("glob_workspace_files", "按 glob 模式查找工作区文件。", _schema({"pattern": {"type": "string", "description": "glob 模式，例如 src/**/*.py"}}, ["pattern"]), _handle_glob_workspace_files, providers=frozenset({"openai", "claude"}), summary_builder=_tool_name_summary("pattern"), category="workspace"))

    registry.register(ToolSpec("grep_workspace_content", "按正则在工作区文件内容中搜索。", _schema({"pattern": {"type": "string", "description": "正则表达式"}, "include": {"type": "string", "description": "可选 glob 文件过滤，例如 *.py"}}, ["pattern"]), _handle_grep_workspace_content, providers=frozenset({"openai", "claude"}), summary_builder=_tool_name_summary("pattern"), category="workspace"))

    registry.register(ToolSpec("bash", "执行 PowerShell 命令，适合多步骤文件操作和自动化任务。", _schema({"command": {"type": "string", "description": "要执行的 PowerShell 命令"}, "workdir": {"type": "string", "description": "可选工作目录，相对于工作区"}, "timeout": {"type": "integer", "description": "超时时间（秒），默认 60"}}, ["command"]), _handle_bash, providers=frozenset({"openai", "claude"}), summary_builder=_tool_command_summary, risk_level="execute", category="workspace", confirmation=_confirm("请确认是否执行 PowerShell 命令：{summary}")))

    registry.register(ToolSpec("web_search", "搜索互联网信息。优先使用 Tavily；未配置 Tavily 时回退到系统默认轻量搜索，能力可能受限。", _schema({"query": {"type": "string", "description": "搜索关键词"}}, ["query"]), _handle_web_search, summary_builder=_tool_name_summary("query"), category="research"))
    registry.register(ToolSpec("send_message", "向当前会话额外发送一条消息。", _schema({"text": {"type": "string", "description": "消息内容"}}, ["text"]), _handle_send_message, summary_builder=_tool_name_summary("text"), risk_level="external", category="communication", confirmation=_confirm("请确认是否发送消息：{summary}")))

    registry.register(ToolSpec("send_file", "向当前会话发送工作区中的一个文件。参数 path 是文件在工作区中的路径。", _schema({"path": {"type": "string", "description": "工作区中的文件路径"}}, ["path"]), _handle_send_file, summary_builder=_tool_name_summary("path"), risk_level="external", category="communication", confirmation=_confirm("请确认是否发送文件：{summary}")))

    registry.register(ToolSpec("get_uploaded_image", "获取本轮上传的图片内容。", _schema({}), _handle_get_uploaded_image, providers=frozenset({"claude"}), availability=_uploaded_image_available, category="media"))

    registry.register(ToolSpec("list_tasks", "列出当前会话下所有待执行的定时任务。", _schema({}), _handle_list_tasks, category="tasks"))

    registry.register(ToolSpec("cancel_task", "取消当前会话下指定 ID 的定时任务。", _schema({"task_id": {"type": "string", "description": "要取消的任务 ID"}}, ["task_id"]), _handle_cancel_task, summary_builder=_tool_name_summary("task_id"), risk_level="write", category="tasks", confirmation=_confirm("请确认是否取消任务：{summary}")))

    registry.register(ToolSpec("create_task", "为当前会话创建一条单次定时任务。run_at 支持 ISO 时间或 YYYY-MM-DD HH:MM:SS。", _schema({"prompt": {"type": "string", "description": "任务内容"}, "run_at": {"type": "string", "description": "执行时间"}}, ["prompt", "run_at"]), _handle_create_task, summary_builder=_tool_name_summary("prompt"), risk_level="write", category="tasks", confirmation=_confirm("请确认是否创建定时任务：{summary}")))

    registry.register(ToolSpec("list_skills", "列出所有可用的 skill，返回名称和描述。", _schema({}), _handle_list_skills, category="skills"))

    registry.register(ToolSpec("read_skill", "读取指定 skill 的完整内容。参数 name 是 skill 的名称。", _schema({"name": {"type": "string", "description": "skill 的名称"}}, ["name"]), _handle_read_skill, summary_builder=_tool_name_summary("name"), category="skills"))

    registry.register(ToolSpec("create_skill", "创建一个新的 skill。参数 name 是 skill 名称（小写字母+下划线），title 是人类可读标题，description 是简短描述，keywords 是逗号分隔的关键词，body 是 skill 的完整指令内容。", _schema({"name": {"type": "string", "description": "skill 名称，只能包含小写字母、数字和下划线"}, "title": {"type": "string", "description": "人类可读的标题"}, "description": {"type": "string", "description": "简短描述"}, "keywords": {"type": "string", "description": "逗号分隔的关键词列表"}, "body": {"type": "string", "description": "skill 的完整指令内容"}}, ["name", "title", "description", "keywords", "body"]), _handle_create_skill, summary_builder=_tool_name_summary("name"), risk_level="write", category="skills", confirmation=_confirm("请确认是否创建 skill：{summary}")))

    registry.register(ToolSpec("update_skill", "更新已有 skill 的内容。参数 name 是 skill 名称，title/description/keywords/body 为可选参数，只更新传入的字段。", _schema({"name": {"type": "string", "description": "skill 名称"}, "title": {"type": "string", "description": "人类可读的标题"}, "description": {"type": "string", "description": "简短描述"}, "keywords": {"type": "string", "description": "逗号分隔的关键词列表"}, "body": {"type": "string", "description": "skill 的完整指令内容"}}, ["name"]), _handle_update_skill, summary_builder=_tool_name_summary("name"), risk_level="write", category="skills", confirmation=_confirm("请确认是否更新 skill：{summary}")))

    registry.register(ToolSpec("delete_skill", "删除指定 skill。参数 name 是 skill 的名称。", _schema({"name": {"type": "string", "description": "skill 名称"}}, ["name"]), _handle_delete_skill, summary_builder=_tool_name_summary("name"), risk_level="destructive", category="skills", confirmation=_confirm("请确认是否删除 skill：{summary}")))

    registry.register(ToolSpec("list_workflows", "列出所有可用的 workflow，返回名称、描述和来源文件。", _schema({}), _handle_list_workflows, category="workflows"))

    registry.register(ToolSpec("read_workflow", "读取指定 workflow 的完整 JSON 定义。参数 name 是 workflow 名称。", _schema({"name": {"type": "string", "description": "workflow 名称"}}, ["name"]), _handle_read_workflow, summary_builder=_tool_name_summary("name"), category="workflows"))

    registry.register(ToolSpec("create_workflow", "创建一个新的 workflow。参数 definition_json 是完整 JSON 定义文本。", _schema({"definition_json": {"type": "string", "description": "workflow 的完整 JSON 定义"}}, ["definition_json"]), _handle_create_workflow, summary_builder=_tool_name_summary("definition_json"), risk_level="write", category="workflows", confirmation=_confirm("请确认是否创建 workflow。")))

    registry.register(ToolSpec("update_workflow", "更新已有 workflow。参数 name 是 workflow 名称，definition_json 是新的完整 JSON 定义文本。", _schema({"name": {"type": "string", "description": "workflow 名称"}, "definition_json": {"type": "string", "description": "workflow 的完整 JSON 定义"}}, ["name", "definition_json"]), _handle_update_workflow, summary_builder=_tool_name_summary("name"), risk_level="write", category="workflows", confirmation=_confirm("请确认是否更新 workflow：{summary}")))

    registry.register(ToolSpec("delete_workflow", "删除指定 workflow。参数 name 是 workflow 名称。", _schema({"name": {"type": "string", "description": "workflow 名称"}}, ["name"]), _handle_delete_workflow, summary_builder=_tool_name_summary("name"), risk_level="destructive", category="workflows", confirmation=_confirm("请确认是否删除 workflow：{summary}")))

    registry.register(ToolSpec("draft_workflow_from_request", "根据自然语言请求草拟 workflow JSON 定义，不写入文件。参数 name 是 workflow 名称，request_text 是自然语言需求，description 可选。", _schema({"name": {"type": "string", "description": "workflow 名称"}, "request_text": {"type": "string", "description": "自然语言 workflow 需求"}, "description": {"type": "string", "description": "可选的 workflow 描述"}}, ["name", "request_text"]), _handle_draft_workflow_from_request, summary_builder=_tool_name_summary("name"), category="workflows"))

    registry.register(ToolSpec("create_workflow_from_request", "根据自然语言请求创建 workflow，并自动写入定义文件。参数 name 是 workflow 名称，request_text 是自然语言需求，description 可选。", _schema({"name": {"type": "string", "description": "workflow 名称"}, "request_text": {"type": "string", "description": "自然语言 workflow 需求"}, "description": {"type": "string", "description": "可选的 workflow 描述"}}, ["name", "request_text"]), _handle_create_workflow_from_request, summary_builder=_tool_name_summary("name"), risk_level="write", category="workflows", confirmation=_confirm("请确认是否根据自然语言创建 workflow：{summary}")))

    registry.register(ToolSpec("update_workflow_from_request", "根据自然语言请求更新已有 workflow。参数 name 是 workflow 名称，request_text 是自然语言需求，description 可选。", _schema({"name": {"type": "string", "description": "workflow 名称"}, "request_text": {"type": "string", "description": "自然语言 workflow 需求"}, "description": {"type": "string", "description": "可选的 workflow 描述"}}, ["name", "request_text"]), _handle_update_workflow_from_request, summary_builder=_tool_name_summary("name"), risk_level="write", category="workflows", confirmation=_confirm("请确认是否根据自然语言更新 workflow：{summary}")))

    return registry





def _register_builtin_workflows(registry: ToolRegistry) -> None:

    from weclaw.capabilities.workflows import build_workflow_handler, load_workflow_report



    report = load_workflow_report()

    for workflow in report.specs:

        registry.register(

            ToolSpec(

                workflow.name,

                workflow.description,

                workflow.parameters,

                build_workflow_handler(workflow, execute_tool),

                providers=workflow.providers,

                summary_builder=_tool_name_summary("name"),

                risk_level=workflow.risk_level,

                category=workflow.category,

                source_path=workflow.source_path,

            )

        )

    return report





def build_tool_registry() -> tuple[ToolRegistry, ToolRegistryStatus]:

    registry = _build_default_registry()

    workflow_report = _register_builtin_workflows(registry)

    status = ToolRegistryStatus(

        loaded_at=datetime.now(),

        workflow_directory=workflow_report.directory,

        workflow_snapshot_entries=workflow_report.snapshot.entries,

        loaded_workflow_files=workflow_report.loaded_files,

    )

    return registry, status





def get_tool_registry() -> ToolRegistry:

    refresh_tool_registry_if_needed()

    return _REGISTRY





def list_tool_specs(provider: str | None = None, context: ToolContext | None = None) -> list[ToolSpec]:

    return get_tool_registry().list(provider=provider, context=context)





def get_tool_spec(name: str) -> ToolSpec | None:

    return get_tool_registry().get(name)





async def execute_tool(name: str, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:

    spec = get_tool_spec(name)

    if spec is None:

        return _error_result(f"错误：未知工具 {name}。")

    record_tool_trace_start(

        ctx.session_scope,

        name=spec.name,

        category=spec.category,

        risk_level=spec.risk_level,

        summary=spec.build_summary(arguments),

        arguments=arguments,

    )

    had_session_grant = bool(ctx.session_scope and has_session_tool_grant(ctx.session_scope, spec.name))

    if spec.requires_confirmation() and ctx.enforce_confirmations:

        if had_session_grant:

            result = await spec.handler(arguments, ctx)

            record_tool_trace_finish(ctx.session_scope, name=spec.name, success=not result.is_error, result_excerpt=result.to_text())

            return result

        if ctx.sender is None:

            result = _error_result(f"错误：工具 {name} 需要确认，但当前上下文无法发起确认。")

            record_tool_trace_finish(ctx.session_scope, name=spec.name, success=False, result_excerpt=result.to_text())

            return result

        prompt = spec.build_confirmation_prompt(arguments) or f"请确认是否执行工具 `{name}`。"

        confirmation_summary = spec.build_summary(arguments)

        _mark_tool_confirmation_waiting(ctx, spec.name, confirmation_summary)

        approved = await request_tool_confirmation(

            ctx.sender,

            ctx.target_id,

            ToolConfirmationRequest(

                tool_name=spec.name,

                summary=confirmation_summary,

                prompt=prompt,

                risk_level=spec.risk_level,

                category=spec.category,

                session_scope=ctx.session_scope or "",

                allow_session_grant=spec.risk_level != "destructive" and bool(ctx.session_scope),

            ),

        )

        if approved is None:

            result = _error_result(f"错误：当前通道暂不支持执行前确认，未执行工具 {name}。")

            record_tool_trace_finish(ctx.session_scope, name=spec.name, success=False, result_excerpt=result.to_text())

            return result

        if not approved:

            _mark_tool_confirmation_rejected(ctx, spec.name, confirmation_summary)

            result = _error_result(f"已取消执行工具 {name}。")

            record_tool_trace_finish(ctx.session_scope, name=spec.name, success=False, result_excerpt=result.to_text())

            return result

    if spec.requires_confirmation() and ctx.enforce_confirmations and not had_session_grant:

        _mark_tool_confirmation_resumed(ctx, spec.name, spec.build_summary(arguments))

    result = await spec.handler(arguments, ctx)

    record_tool_trace_finish(ctx.session_scope, name=spec.name, success=not result.is_error, result_excerpt=result.to_text())

    return result





def _mark_tool_confirmation_waiting(ctx: ToolContext, tool_name: str, summary: str) -> None:

    waiting_text = _confirmation_waiting_text(tool_name, summary)

    if ctx.conversation:

        mark_agent_waiting(ctx.conversation, waiting_text)

    cluster_context = _cluster_context_from_session_scope(ctx.session_scope)

    if cluster_context:

        cluster_id, agent_id, _task_id = cluster_context

        mark_cluster_agent_waiting(cluster_id, agent_id, waiting_text)





def _mark_tool_confirmation_resumed(ctx: ToolContext, tool_name: str, summary: str) -> None:

    resume_text = _compact_summary(f"\u7ee7\u7eed\u6267\u884c\u5de5\u5177\uff1a{tool_name} {summary}".strip())

    if ctx.conversation:

        mark_agent_tool_started(ctx.conversation, tool_name, resume_text)

    cluster_context = _cluster_context_from_session_scope(ctx.session_scope)

    if cluster_context:

        cluster_id, agent_id, task_id = cluster_context

        mark_cluster_task_started(cluster_id, task_id, agent_id, resume_text)





def _mark_tool_confirmation_rejected(ctx: ToolContext, tool_name: str, summary: str) -> None:

    rejected_text = _compact_summary(f"\u5df2\u53d6\u6d88\u5de5\u5177\uff1a{tool_name} {summary}".strip())

    if ctx.conversation:

        mark_agent_tool_cancelled(ctx.conversation, tool_name, rejected_text)

    cluster_context = _cluster_context_from_session_scope(ctx.session_scope)

    if cluster_context:

        cluster_id, agent_id, task_id = cluster_context

        mark_cluster_task_finished(cluster_id, task_id, agent_id, rejected_text, success=False)





def _confirmation_waiting_text(tool_name: str, summary: str) -> str:

    return _compact_summary(f"\u9700\u8981\u6388\u6743\uff1a{tool_name} {summary}".strip())





def _compact_summary(value: str, max_length: int = 160) -> str:

    normalized = " ".join(str(value or "").split())

    if len(normalized) <= max_length:

        return normalized

    return normalized[: max_length - 1] + "\u2026"





def _cluster_context_from_session_scope(session_scope: str | None) -> tuple[str, str, str] | None:

    if not session_scope or ":cluster:" not in session_scope:

        return None

    tail = session_scope.split(":cluster:", 1)[1]

    parts = tail.split(":", 2)

    if len(parts) != 3 or not all(parts):

        return None

    cluster_id, agent_id, task_id = parts

    return cluster_id, agent_id, task_id





_REGISTRY, _REGISTRY_STATUS = build_tool_registry()





def rebuild_tool_registry() -> ToolRegistry:

    global _REGISTRY, _REGISTRY_STATUS

    _REGISTRY, _REGISTRY_STATUS = build_tool_registry()

    return _REGISTRY





def _current_workflow_snapshot_entries() -> tuple[tuple[str, int, int], ...]:

    from weclaw.capabilities.workflows import snapshot_workflow_definitions



    return snapshot_workflow_definitions().entries





def refresh_tool_registry_if_needed() -> ToolRegistry:

    global _REGISTRY, _REGISTRY_STATUS

    current_entries = _current_workflow_snapshot_entries()

    if current_entries == _REGISTRY_STATUS.workflow_snapshot_entries:

        return _REGISTRY

    try:

        _REGISTRY, _REGISTRY_STATUS = build_tool_registry()

    except Exception as exc:

        _REGISTRY_STATUS = ToolRegistryStatus(

            loaded_at=_REGISTRY_STATUS.loaded_at,

            workflow_directory=_REGISTRY_STATUS.workflow_directory,

            workflow_snapshot_entries=_REGISTRY_STATUS.workflow_snapshot_entries,

            loaded_workflow_files=_REGISTRY_STATUS.loaded_workflow_files,

            last_error=str(exc) or exc.__class__.__name__,

            failed_at=datetime.now(),

        )

    return _REGISTRY





def get_tool_registry_status() -> ToolRegistryStatus:

    refresh_tool_registry_if_needed()

    return _REGISTRY_STATUS





def build_openai_tool_definitions(ctx: ToolContext | None = None, allowed_names: set[str] | None = None) -> list[dict[str, Any]]:

    specs = list_tool_specs(provider="openai", context=ctx)

    if allowed_names is not None and allowed_names:

        specs = [spec for spec in specs if spec.name in allowed_names]

    return [spec.build_openai_definition() for spec in specs]





def build_claude_allowed_tools(base_tools: list[str] | None = None, ctx: ToolContext | None = None) -> list[str]:

    ordered: list[str] = list(base_tools or [])

    seen = set(ordered)

    for spec in list_tool_specs(provider="claude", context=ctx):

        for name in (spec.name, f"mcp__weclaw__{spec.name}"):

            if name not in seen:

                ordered.append(name)

                seen.add(name)

    return ordered





def parse_openai_allowed_tools(raw_value: str | None = None) -> set[str]:

    text = config.OPENAI_ALLOWED_TOOLS if raw_value is None else raw_value

    return {item.strip() for item in text.split(",") if item.strip()}





def list_openai_tool_names(ctx: ToolContext | None = None, allowed_names: set[str] | None = None) -> list[str]:

    specs = list_tool_specs(provider="openai", context=ctx)

    if allowed_names is not None and allowed_names:

        specs = [spec for spec in specs if spec.name in allowed_names]

    return [spec.name for spec in specs]
