from datetime import datetime, timedelta, timezone
import logging

from telegram import Update
from telegram.error import BadRequest, NetworkError, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from garveyclaw.access import is_owner
from garveyclaw.claude_client import ClaudeServiceError, ask_claude
from garveyclaw.config import TELEGRAM_BOT_TOKEN
from garveyclaw.memory_store import append_long_term_memory, load_long_term_memory
from garveyclaw.scheduler import (
    create_scheduled_task,
    format_schedule_description,
    list_scheduled_tasks,
    parse_natural_schedule,
    cancel_scheduled_task,
    setup_scheduler,
)
from garveyclaw.scheduler_store import init_task_db
from garveyclaw.session_store import clear_session_id
from garveyclaw.telegram_formatting import format_telegram_text

logger = logging.getLogger(__name__)


async def reply_plain_text(update: Update, text: str) -> None:
    # 兜底纯文本回复，用于错误提示或格式化回退。
    if not update.message:
        return

    await update.message.reply_text(text, disable_web_page_preview=True)


async def reply_formatted_text(update: Update, text: str) -> None:
    # 正常情况下优先发送格式化文本。
    if not update.message:
        return

    for chunk in format_telegram_text(text):
        try:
            await update.message.reply_text(
                chunk["text"],
                parse_mode=chunk["parse_mode"],
                disable_web_page_preview=True,
            )
        except BadRequest:
            # Telegram 无法解析格式化内容时，回退到纯文本。
            logger.warning("Telegram formatted reply failed, falling back to plain text", exc_info=True)
            await reply_plain_text(update, text)
            return


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # 非 owner 或非文本消息直接忽略，不进入模型调用链路。
    if not update.message or not update.message.text:
        return
    if not is_owner(update):
        return

    try:
        natural_schedule = parse_natural_schedule(update.message.text)
        if natural_schedule is not None:
            chat_id = update.effective_chat.id if update.effective_chat else None
            if chat_id is None:
                await reply_plain_text(update, "当前消息没有可用的 chat_id。")
                return

            task_id = await create_scheduled_task(
                chat_id=chat_id,
                prompt=natural_schedule.prompt,
                run_at=natural_schedule.run_at,
                schedule_type=natural_schedule.schedule_type,
                schedule_value=natural_schedule.schedule_value,
            )
            local_time = natural_schedule.run_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            await reply_plain_text(
                update,
                "我已按自然语言理解为一条定时任务。\n"
                f"- 任务ID：{task_id}\n"
                f"- 类型：{format_schedule_description(natural_schedule.schedule_type, natural_schedule.schedule_value)}\n"
                f"- 执行时间：{local_time}\n"
                f"- 内容：{natural_schedule.prompt}",
            )
            return

        response = await ask_claude(update.message.text, update)
        await reply_formatted_text(update, response)
    except ClaudeServiceError:
        await reply_plain_text(update, "抱歉，这次调用模型服务失败了。请稍后再试一次。")
    except NetworkError:
        logger.warning("Telegram network error while handling message", exc_info=True)
        await reply_plain_text(update, "抱歉，当前网络连接不稳定，请稍后重试。")
    except TelegramError:
        logger.exception("Telegram API error while handling message")
        await reply_plain_text(update, "抱歉，消息发送失败了。请稍后重试。")
    except Exception:
        logger.exception("Unexpected error while handling message")
        await reply_plain_text(update, "抱歉，机器人刚刚出了点问题。请稍后再试。")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # 只对 owner 响应 /start，其余用户保持静默。
    if not update.message:
        return
    if not is_owner(update):
        return

    await update.message.reply_text(
        "你好，我是你的机器人。\n\n"
        "我可以回答问题、使用内置 Claude 工具、操作工作区，并继续之前保存的会话。\n"
        "还支持定时任务，例如“30秒后提醒我喝水”“每天下午3点提醒我站起来活动一下”。\n"
        "可以使用 /memory 查看长期记忆，使用 /remember 追加长期记忆，使用 /reset 清空当前会话，"
        "使用 /schedule_in、/tasks、/cancel 管理定时任务。"
    )


async def reset_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # 清空本地保存的 session_id，下一次消息会从新会话开始。
    if not update.message:
        return
    if not is_owner(update):
        return

    clear_session_id()
    await update.message.reply_text("当前会话已清空，下一条消息会开启新会话。")


async def show_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # 查看当前长期记忆文件的内容。
    if not update.message:
        return
    if not is_owner(update):
        return

    await reply_plain_text(update, load_long_term_memory())


async def remember(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # 把一条用户指定的内容追加到长期记忆文件中。
    if not update.message:
        return
    if not is_owner(update):
        return

    memory_note = " ".join(context.args).strip()
    if not memory_note:
        await reply_plain_text(update, "用法：/remember 这里填写要写入长期记忆的内容")
        return

    append_long_term_memory(memory_note)
    await reply_plain_text(update, "长期记忆已更新。")


async def schedule_in(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # 命令式创建一条若干秒后执行的单次定时任务。
    if not update.message:
        return
    if not is_owner(update):
        return

    if len(context.args) < 2:
        await reply_plain_text(update, "用法：/schedule_in 秒数 任务内容")
        return

    try:
        delay_seconds = int(context.args[0])
    except ValueError:
        await reply_plain_text(update, "秒数必须是整数，例如：/schedule_in 60 1分钟后提醒我喝水")
        return

    if delay_seconds <= 0:
        await reply_plain_text(update, "秒数必须大于 0。")
        return

    prompt = " ".join(context.args[1:]).strip()
    if not prompt:
        await reply_plain_text(update, "任务内容不能为空。")
        return

    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        await reply_plain_text(update, "当前消息没有可用的 chat_id。")
        return

    run_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
    task_id = await create_scheduled_task(chat_id, prompt, run_at)
    local_time = run_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    await reply_plain_text(
        update,
        "定时任务已创建。\n"
        f"- 任务ID：{task_id}\n"
        "- 类型：单次任务\n"
        f"- 执行时间：{local_time}\n"
        f"- 内容：{prompt}",
    )


async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # 列出当前所有 active 状态的定时任务。
    if not update.message:
        return
    if not is_owner(update):
        return

    tasks = await list_scheduled_tasks()
    if not tasks:
        await reply_plain_text(update, "当前没有待执行的定时任务。")
        return

    lines = ["当前待执行任务："]
    for task in tasks:
        local_time = datetime.fromisoformat(task["next_run"]).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        schedule_desc = format_schedule_description(task.get("schedule_type", "once"), task.get("schedule_value"))
        lines.append(f"- {task['id']} | {schedule_desc} | {local_time} | {task['prompt']}")

    await reply_plain_text(update, "\n".join(lines))


async def cancel_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # 取消一条未执行的定时任务。
    if not update.message:
        return
    if not is_owner(update):
        return

    if not context.args:
        await reply_plain_text(update, "用法：/cancel 任务ID")
        return

    task_id = context.args[0].strip()
    success = await cancel_scheduled_task(task_id)
    if success:
        await reply_plain_text(update, f"任务 {task_id} 已取消。")
    else:
        await reply_plain_text(update, f"没有找到可取消的任务：{task_id}")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    # 捕获没有被局部 handler 处理的异常，便于统一排查。
    logger.exception("Unhandled exception in Telegram application", exc_info=context.error)


async def post_init(application: Application) -> None:
    # 启动时初始化任务数据库并拉起调度器。
    await init_task_db()
    scheduler = setup_scheduler(application.bot)
    scheduler.start()
    application.bot_data["scheduler"] = scheduler


def build_application() -> Application:
    # 创建 Telegram 应用并注册命令、消息和全局错误处理器。
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("memory", show_memory))
    app.add_handler(CommandHandler("remember", remember))
    app.add_handler(CommandHandler("reset", reset_session))
    app.add_handler(CommandHandler("schedule_in", schedule_in))
    app.add_handler(CommandHandler("tasks", list_tasks))
    app.add_handler(CommandHandler("cancel", cancel_task))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)
    return app
