from __future__ import annotations



import re

from dataclasses import dataclass

from datetime import datetime, timedelta, timezone



from weclaw.core.types import ConversationRef

from weclaw.tasks.scheduler import (

    format_schedule_description,

    parse_natural_schedule,

)

from weclaw.tasks.repository import (

    cancel_scheduled_task_record,

    create_scheduled_task_record,

    list_scheduled_task_records,

)

import uuid





CANCEL_INTENT_PATTERNS: tuple[tuple[re.Pattern[str], bool], ...] = (

    # "取消第X个" 模式放在最前面，优先匹配

    (re.compile(r"^取消第(?P<index>\d+)个$"), False),

    (re.compile(r"^取消第(?P<index_word>一|二|三|四|五|六|七|八|九|十)个$"), False),

    (re.compile(r"^第(?P<index_only>\d+)个$"), False),

    (re.compile(r"^第(?P<index_word_only>一|二|三|四|五|六|七|八|九|十)个$"), False),

    # "别...了"、"不要...了" 这种表达通常是模糊取消

    (re.compile(r"^(?:别|不要|不用|不需要).+了$"), False),

    # 更具体的模式

    (re.compile(r"^(?:取消提醒|取消任务|取消定时|别提醒|不要提醒|不用提醒|别执行|不要执行)(?P<target>.*)$"), False),

    (re.compile(r"^(?:把(?P<target>.+)取消|把(?P<target2>.+)取消掉|把(?P<target3>.+)取消了吧)"), True),

    (re.compile(r"^(?:取消|取消掉|别提醒了|不要提醒了|不用提醒了|别执行了|不要执行了)$"), False),

    (re.compile(r"^(?:取消|取消掉|帮我把)[：:，,\s]*(?P<target>.+)$"), True),

    (re.compile(r"^(?:别|不要|不用|不需要)[：:，,\s]*(?P<target>.+)$"), True),

)



ORDINAL_WORD_TO_INDEX = {

    "一": 1,

    "二": 2,

    "三": 3,

    "四": 4,

    "五": 5,

    "六": 6,

    "七": 7,

    "八": 8,

    "九": 9,

    "十": 10,

}



TASK_LIST_INTENT_PATTERNS: tuple[re.Pattern[str], ...] = (

    re.compile(r"^(?:当前)?(?:有|还有)?哪些(?:定时)?任务\??$"),

    re.compile(r"^(?:看看|查看|列出)(?:一下)?(?:当前)?(?:的)?(?:定时)?任务$"),

    re.compile(r"^(?:我)?(?:现在)?(?:有|还有)几个(?:定时)?任务\??$"),

    re.compile(r"^(?:我)?(?:现在)?(?:有|还有)哪些提醒\??$"),

    re.compile(r"^(?:看看|查看|列出)(?:一下)?(?:当前)?(?:的)?提醒$"),

)





def detect_cancel_intent(text: str) -> str | None:

    """检测用户是否有取消任务的意图。

    

    返回：

    - 如果有明确取消目标，返回目标描述

    - 如果是"取消第X个"，返回 "index:X" 格式

    - 如果是模糊取消意图（如"别提醒我了"），返回 "" 表示需要列出任务

    - 如果没有取消意图，返回 None

    """

    stripped = text.strip()

    if not stripped:

        return None

    

    lowered = stripped.lower()

    

    # 明确的取消命令（带任务 ID）

    if lowered.startswith("/cancel"):

        return None  # 由 /cancel 命令处理

    

    # 检查是否匹配取消意图模式

    for pattern, requires_target in CANCEL_INTENT_PATTERNS:

        match = pattern.match(stripped)

        if match:

            # 检查是否是"取消第X个"模式

            try:

                index = match.group("index")

                if index:

                    return f"index:{index}"

            except IndexError:

                pass

            try:

                index_word = match.group("index_word")

                if index_word:

                    return f"index:{ORDINAL_WORD_TO_INDEX[index_word]}"

            except IndexError:

                pass

            try:

                index_only = match.group("index_only")

                if index_only:

                    return f"index:{index_only}"

            except IndexError:

                pass

            try:

                index_word_only = match.group("index_word_only")

                if index_word_only:

                    return f"index:{ORDINAL_WORD_TO_INDEX[index_word_only]}"

            except IndexError:

                pass

            

            # 尝试获取 target 组（可能有多个命名）

            try:

                target = match.group("target") or match.group("target2") or match.group("target3")

            except IndexError:

                target = None



            if target and target.strip():

                if target.strip() in {"定时任务", "任务", "提醒", "那个定时任务", "那个任务", "这个定时任务", "这个任务"}:

                    return ""

                # 防止误判：以普通操作词开头的不要当取消

                lowered_target = target.strip()

                if any(lowered_target.startswith(word) for word in ("查", "搜", "天气", "新闻", "几点", "什么")):

                    return None

                return target.strip()

            # 没有明确目标，返回空字符串表示需要列出任务

            return ""

    

    return None





def has_task_list_intent(text: str) -> bool:

    stripped = text.strip()

    if not stripped:

        return False

    return any(pattern.match(stripped) for pattern in TASK_LIST_INTENT_PATTERNS)





async def _cancel_task_by_description(target: str, conversation: ConversationRef) -> TaskCommandResult:

    """根据描述取消任务（模糊匹配任务内容）。"""

    tasks = await list_scheduled_tasks(conversation.channel, conversation.target_id)

    if not tasks:

        return TaskCommandResult(True, "当前没有待执行的任务可以取消。")

    

    # 检查是否是"取消第X个"格式

    if target.startswith("index:"):

        try:

            index = int(target.split(":")[1])

            if index < 1 or index > len(tasks):

                return TaskCommandResult(True, f"序号无效，当前只有 {len(tasks)} 个任务。")

            task = tasks[index - 1]

            success = await cancel_scheduled_task(task["id"], conversation.channel, conversation.target_id)

            if success:

                return TaskCommandResult(True, f"好的，已取消：{build_task_display_text(task['prompt'])}")

            return TaskCommandResult(True, "取消失败，请稍后再试。")

        except ValueError:

            return TaskCommandResult(True, "序号格式错误，请说'取消第X个'。")

    

    # 模糊匹配：检查任务内容是否包含目标关键词

    matched_tasks = []

    target_lower = target.lower()

    for task in tasks:

        if target_lower in build_task_display_text(task["prompt"]).lower():

            matched_tasks.append(task)

    

    if not matched_tasks:

        # 没有匹配到，列出所有任务让用户选择

        lines = ["没有找到匹配的任务，你当前的定时任务："]

        for index, task in enumerate(tasks, 1):

            local_time = datetime.fromisoformat(task["next_run"]).astimezone().strftime("%Y-%m-%d %H:%M:%S")

            schedule_desc = format_schedule_description(task.get("schedule_type", "once"), task.get("schedule_value"))

            lines.append(f"{index}. {local_time} | {schedule_desc} | {build_task_display_text(task['prompt'])}")

        lines.append("\n请告诉我具体要取消哪个，比如说'取消第1个'或'取消第一个'。")

        return TaskCommandResult(True, "\n".join(lines))

    

    if len(matched_tasks) == 1:

        # 只匹配到一个，直接取消

        task = matched_tasks[0]

        success = await cancel_scheduled_task(task["id"], conversation.channel, conversation.target_id)

        if success:

            return TaskCommandResult(True, f"好的，已取消：{build_task_display_text(task['prompt'])}")

        return TaskCommandResult(True, "取消失败，请稍后再试。")

    

    # 匹配到多个，列出让用户选择

    lines = [f"找到 {len(matched_tasks)} 个匹配的任务："]

    for i, task in enumerate(matched_tasks, 1):

        local_time = datetime.fromisoformat(task["next_run"]).astimezone().strftime("%Y-%m-%d %H:%M:%S")

        schedule_desc = format_schedule_description(task.get("schedule_type", "once"), task.get("schedule_value"))

        lines.append(f"{i}. {local_time} | {schedule_desc} | {build_task_display_text(task['prompt'])}")

    lines.append("\n请告诉我具体要取消哪个，比如说'取消第1个'或'取消第一个'。")

    return TaskCommandResult(True, "\n".join(lines))





async def _list_all_tasks_for_cancel(conversation: ConversationRef) -> TaskCommandResult:

    """列出所有任务让用户选择取消。"""

    tasks = await list_scheduled_tasks(conversation.channel, conversation.target_id)

    if not tasks:

        return TaskCommandResult(True, "当前没有待执行的任务可以取消。")

    

    lines = ["你当前的定时任务："]

    for i, task in enumerate(tasks, 1):

        local_time = datetime.fromisoformat(task["next_run"]).astimezone().strftime("%Y-%m-%d %H:%M:%S")

        schedule_desc = format_schedule_description(task.get("schedule_type", "once"), task.get("schedule_value"))

        lines.append(f"{i}. {local_time} | {schedule_desc} | {build_task_display_text(task['prompt'])}")

    lines.append("\n请告诉我具体要取消哪个，比如说'取消第1个'或'取消第一个'。")

    return TaskCommandResult(True, "\n".join(lines))





async def create_scheduled_task(

    conversation: ConversationRef,

    prompt: str,

    run_at: datetime,

    schedule_type: str = "once",

    schedule_value: str | None = None,

    continue_session: bool = False,

) -> str:

    task_id = uuid.uuid4().hex[:8]

    await create_scheduled_task_record(

        task_id=task_id,

        conversation=conversation,

        prompt=prompt,

        run_at=run_at,

        schedule_type=schedule_type,

        schedule_value=schedule_value,

        continue_session=continue_session,

    )

    return task_id





async def list_scheduled_tasks(channel: str | None = None, target_id: str | None = None):

    return await list_scheduled_task_records(channel=channel, target_id=target_id)





async def cancel_scheduled_task(task_id: str, channel: str | None = None, target_id: str | None = None) -> bool:

    return await cancel_scheduled_task_record(task_id, channel=channel, target_id=target_id)





def build_task_confirmation_text(prompt: str) -> str:

    normalized = prompt.strip()

    normalized = re.sub(r"^(提醒我|提醒一下我|叫我|喊我|通知我)", "", normalized).strip()

    if normalized and normalized != prompt.strip():

        return f"提醒你{normalized}"

    return f"提醒你：{prompt.strip()}"





def build_task_display_text(prompt: str) -> str:

    normalized = prompt.strip()

    if "任务内容：" in normalized:

        normalized = normalized.split("任务内容：", maxsplit=1)[-1].strip()

    normalized = re.sub(r"^你正在执行一条定时任务。.*$", "", normalized, flags=re.DOTALL).strip()

    return normalized or prompt.strip()





@dataclass(frozen=True, slots=True)

class TaskCommandResult:

    handled: bool

    message: str = ""





async def handle_task_command(conversation: ConversationRef, text: str) -> TaskCommandResult:

    stripped = text.strip()

    lowered = stripped.lower()



    if lowered.startswith("/schedule_in"):

        parts = stripped.split(maxsplit=2)

        if len(parts) < 3:

            return TaskCommandResult(True, "用法：/schedule_in 秒数 任务内容")

        try:

            delay_seconds = int(parts[1])

        except ValueError:

            return TaskCommandResult(True, "秒数必须是整数，例如：/schedule_in 60 1分钟后提醒我喝水")

        if delay_seconds <= 0:

            return TaskCommandResult(True, "秒数必须大于 0。")

        prompt = parts[2].strip()

        if not prompt:

            return TaskCommandResult(True, "任务内容不能为空。")

        run_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)

        await create_scheduled_task(conversation, prompt, run_at)

        local_time = run_at.astimezone().strftime("%H:%M:%S")

        return TaskCommandResult(

            True,

            f"好的，{delay_seconds} 秒后（{local_time}）{build_task_confirmation_text(prompt)}",

        )



    if lowered.startswith("/schedule"):

        schedule_text = stripped[len("/schedule") :].strip()

        if not schedule_text:

            return TaskCommandResult(True, "用法：/schedule 自然语言时间 + 任务内容，例如：/schedule 每天下午3点提醒我喝水")

        natural_schedule = parse_natural_schedule(schedule_text)

        if natural_schedule is None:

            return TaskCommandResult(True, "没有识别出有效的定时表达。示例：/schedule 每天下午3点提醒我喝水")

        await create_scheduled_task(

            conversation=conversation,

            prompt=natural_schedule.prompt,

            run_at=natural_schedule.run_at,

            schedule_type=natural_schedule.schedule_type,

            schedule_value=natural_schedule.schedule_value,

        )

        local_time = natural_schedule.run_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")

        

        schedule_type = natural_schedule.schedule_type

        if schedule_type == "once":

            return TaskCommandResult(

                True,

                f"好的，{local_time} {build_task_confirmation_text(natural_schedule.prompt)}",

            )

        elif schedule_type == "daily":

            return TaskCommandResult(

                True,

                f"好的，每天 {local_time.split(' ')[-1]} {build_task_confirmation_text(natural_schedule.prompt)}",

            )

        elif schedule_type == "weekly":

            return TaskCommandResult(

                True,

                f"好的，每周 {local_time} {build_task_confirmation_text(natural_schedule.prompt)}",

            )

        else:

            return TaskCommandResult(

                True,

                f"好的，{local_time} 执行 {natural_schedule.prompt}",

            )



    if lowered == "/tasks" or has_task_list_intent(stripped):

        tasks = await list_scheduled_tasks(conversation.channel, conversation.target_id)

        if not tasks:

            return TaskCommandResult(True, "当前没有待执行的定时任务。")

        lines = ["你当前的定时任务："]

        for index, task in enumerate(tasks, 1):

            local_time = datetime.fromisoformat(task["next_run"]).astimezone().strftime("%Y-%m-%d %H:%M:%S")

            schedule_desc = format_schedule_description(task.get("schedule_type", "once"), task.get("schedule_value"))

            lines.append(f"{index}. {local_time} | {schedule_desc} | {build_task_display_text(task['prompt'])}")

        return TaskCommandResult(True, "\n".join(lines))



    if lowered.startswith("/cancel"):

        parts = stripped.split(maxsplit=1)

        if len(parts) < 2 or not parts[1].strip():

            return TaskCommandResult(True, "用法：/cancel 任务ID")

        task_id = parts[1].strip()

        success = await cancel_scheduled_task(task_id, conversation.channel, conversation.target_id)

        return TaskCommandResult(True, "任务已取消。" if success else f"没有找到可取消的任务：{task_id}")



    # 自然语言取消意图检测

    cancel_target = detect_cancel_intent(stripped)

    if cancel_target is not None:

        if cancel_target:

            # 有明确的取消目标

            return await _cancel_task_by_description(cancel_target, conversation)

        else:

            # 模糊取消意图，列出所有任务

            return await _list_all_tasks_for_cancel(conversation)



    natural_schedule = parse_natural_schedule(stripped)

    if natural_schedule is None:

        return TaskCommandResult(False)

    await create_scheduled_task(

        conversation=conversation,

        prompt=natural_schedule.prompt,

        run_at=natural_schedule.run_at,

        schedule_type=natural_schedule.schedule_type,

        schedule_value=natural_schedule.schedule_value,

    )

    local_time = natural_schedule.run_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")

    

    # 自然语言风格的确认回复

    schedule_type = natural_schedule.schedule_type

    if schedule_type == "once":

        return TaskCommandResult(

            True,

            f"好的，{local_time} {build_task_confirmation_text(natural_schedule.prompt)}",

        )

    elif schedule_type == "daily":

        return TaskCommandResult(

            True,

            f"好的，每天 {local_time.split(' ')[-1]} {build_task_confirmation_text(natural_schedule.prompt)}",

        )

    elif schedule_type == "weekly":

        return TaskCommandResult(

            True,

            f"好的，每周 {local_time} {build_task_confirmation_text(natural_schedule.prompt)}",

        )

    else:

        return TaskCommandResult(

            True,

            f"好的，已设置定时任务：{local_time} 执行 {natural_schedule.prompt}",

        )
