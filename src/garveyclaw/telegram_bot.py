from __future__ import annotations

import logging
from io import BytesIO
from datetime import datetime, timedelta, timezone

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
from garveyclaw.agent_response import AgentReply
from garveyclaw.agent_client import AgentServiceError, ask_agent
from garveyclaw.config import (
    TELEGRAM_BOOTSTRAP_RETRIES,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CONNECT_TIMEOUT,
    TELEGRAM_POLLING_TIMEOUT,
    TELEGRAM_POOL_TIMEOUT,
    TELEGRAM_READ_TIMEOUT,
    TELEGRAM_WRITE_TIMEOUT,
)
from garveyclaw.media_store import load_photo_message, save_voice_message
from garveyclaw.memory_store import append_long_term_memory, load_long_term_memory
from garveyclaw.scheduler import (
    cancel_scheduled_task,
    create_scheduled_task,
    format_schedule_description,
    list_scheduled_tasks,
    parse_natural_schedule,
    setup_scheduler,
)
from garveyclaw.scheduler_store import init_task_db
from garveyclaw.session_store import clear_session_id
from garveyclaw.skill_store import get_skill, list_skills
from garveyclaw.speech_client import SpeechRecognitionError, transcribe_voice
from garveyclaw.telegram_formatting import format_telegram_text

logger = logging.getLogger(__name__)


async def reply_plain_text(update: Update, text: str) -> None:
    """发送纯文本回复，用于错误提示或格式化回退。"""

    if not update.message:
        return

    await update.message.reply_text(text, disable_web_page_preview=True)


async def reply_formatted_text(update: Update, text: str) -> None:
    """优先发送格式化文本，失败时回退到纯文本。"""

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
            logger.warning("Telegram formatted reply failed, falling back to plain text", exc_info=True)
            await reply_plain_text(update, text)
            return


async def reply_agent_result(update: Update, reply: AgentReply) -> None:
    """统一发送 Agent 返回结果：先发图片，再发文本说明。"""

    if not update.message:
        return

    for image in reply.images:
        image_file = BytesIO(image.data)
        image_file.name = f"generated.{image.mime_type.removeprefix('image/')}"
        await update.message.reply_photo(photo=image_file, caption=image.caption)

    if reply.text.strip():
        await reply_formatted_text(update, reply.text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理普通文本消息。"""

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

        response = await ask_agent(update.message.text, update)
        await reply_agent_result(update, response)
    except AgentServiceError as exc:
        await reply_plain_text(update, f"抱歉，这次调用模型服务失败了：{exc}")
    except NetworkError:
        logger.warning("Telegram network error while handling message", exc_info=True)
        await reply_plain_text(update, "抱歉，当前网络连接不稳定，请稍后重试。")
    except TelegramError:
        logger.exception("Telegram API error while handling message")
        await reply_plain_text(update, "抱歉，消息发送失败了。请稍后重试。")
    except Exception:
        logger.exception("Unexpected error while handling message")
        await reply_plain_text(update, "抱歉，机器人刚刚出了点问题。请稍后再试。")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理图片消息：图片先进入内存，再直接交给 Agent。"""

    if not update.message:
        return
    if not is_owner(update):
        return

    try:
        photo_payload = await load_photo_message(update.message)
        caption = (update.message.caption or "").strip()
        caption_text = caption or "无"
        prompt = (
            "用户上传了一张图片。\n"
            f"用户附带说明：{caption_text}\n\n"
            "请先调用 get_uploaded_image 工具获取本轮图片内容，"
            "再结合图片和用户说明进行分析，并直接给出有帮助的中文回答。"
        )
        record_text = f"用户上传了一张图片。说明：{caption_text}"
        response = await ask_agent(
            prompt=prompt,
            update=update,
            record_text=record_text,
            uploaded_image=photo_payload,
        )
        await reply_agent_result(update, response)
    except AgentServiceError as exc:
        await reply_plain_text(update, f"抱歉，这次图片处理失败了：{exc}")
    except NetworkError:
        logger.warning("Telegram network error while handling photo", exc_info=True)
        await reply_plain_text(update, "抱歉，当前网络连接不稳定，图片处理失败。请稍后重试。")
    except TelegramError:
        logger.exception("Telegram API error while handling photo")
        await reply_plain_text(update, "抱歉，图片下载或消息发送失败了。请稍后重试。")
    except Exception:
        logger.exception("Unexpected error while handling photo")
        await reply_plain_text(update, "抱歉，机器人处理图片时出了点问题。请稍后再试。")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理语音消息：先转写，再把文字内容交给 Agent。"""

    if not update.message:
        return
    if not is_owner(update):
        return

    try:
        voice_path = await save_voice_message(update.message)
        transcript = transcribe_voice(voice_path)
        prompt = (
            "用户发送了一条语音消息。\n"
            f"语音本地路径：{voice_path}\n"
            f"语音转写文本：{transcript}\n\n"
            "请把这条语音转写文本当作用户的真实输入来处理。"
        )
        response = await ask_agent(prompt, update)
        await reply_agent_result(update, response)
    except SpeechRecognitionError as exc:
        logger.warning("Speech recognition failed", exc_info=True)
        await reply_plain_text(update, f"语音已保存，但语音转文字失败：{exc}")
    except AgentServiceError as exc:
        await reply_plain_text(update, f"语音已转写，但这次调用模型服务失败了：{exc}")
    except NetworkError:
        logger.warning("Telegram network error while handling voice", exc_info=True)
        await reply_plain_text(update, "抱歉，当前网络连接不稳定，语音处理失败。请稍后重试。")
    except TelegramError:
        logger.exception("Telegram API error while handling voice")
        await reply_plain_text(update, "抱歉，语音下载或消息发送失败了。请稍后重试。")
    except Exception:
        logger.exception("Unexpected error while handling voice")
        await reply_plain_text(update, "抱歉，机器人处理语音时出了点问题。请稍后再试。")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """响应 /start，介绍当前机器人支持的主要能力。"""

    if not update.message:
        return
    if not is_owner(update):
        return

    await update.message.reply_text(
        "你好，我是你的机器人。\n\n"
        "我可以回答问题、处理文字、图片和语音消息，使用 Claude 内置工具，操作工作区，并继续之前保存的会话。\n"
        "还支持定时任务，例如“30秒后提醒我喝水”“每天下午3点提醒我站起来活动一下”。\n"
        "可以使用 /memory 查看长期记忆，使用 /remember 追加长期记忆，使用 /reset 清空当前会话。\n"
        "使用 /skills 查看当前可用的 skills。\n"
        "使用 /schedule_in、/tasks、/cancel 管理定时任务。"
    )


async def reset_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """清空当前 session，让下一条消息从新会话开始。"""

    if not update.message:
        return
    if not is_owner(update):
        return

    clear_session_id()
    await update.message.reply_text("当前会话已清空，下一条消息会开启新会话。")


async def show_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """查看当前长期记忆内容。"""

    if not update.message:
        return
    if not is_owner(update):
        return

    await reply_plain_text(update, load_long_term_memory())


async def remember(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """把用户指定内容追加到长期记忆。"""

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


async def show_skills(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """查看当前可用的 skills，或查看某个 skill 的详细说明。"""

    if not update.message:
        return
    if not is_owner(update):
        return

    if not context.args:
        lines = ["当前可用的 skills："]
        for skill in list_skills():
            lines.append(f"- {skill.name}：{skill.description}")
        lines.append("\n你也可以发送 /skills skill_name 查看单个 skill 的详情。")
        await reply_plain_text(update, "\n".join(lines))
        return

    skill_name = context.args[0].strip().lower()
    skill = get_skill(skill_name)
    if skill is None:
        await reply_plain_text(update, f"没有找到名为 {skill_name} 的 skill。")
        return

    detail = skill.file_path.read_text(encoding="utf-8").strip() if skill.file_path.exists() else "这个 skill 文件暂时不存在。"
    await reply_plain_text(
        update,
        f"Skill: {skill.name}\n"
        f"标题：{skill.title}\n"
        f"说明：{skill.description}\n\n"
        f"{detail}",
    )


async def schedule_in(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """通过命令创建一个单次定时任务。"""

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
    """列出当前待执行的定时任务。"""

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
    """取消一个未执行的定时任务。"""

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
    """兜底记录没有被局部 handler 处理的异常。"""

    logger.error("Unhandled exception in Telegram application", exc_info=context.error)


async def post_init(application: Application) -> None:
    """启动时初始化定时任务数据库并拉起调度器。"""

    await init_task_db()
    scheduler = setup_scheduler(application.bot)
    scheduler.start()
    application.bot_data["scheduler"] = scheduler


def build_application() -> Application:
    """创建 Telegram 应用并注册命令、消息和错误处理器。"""

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .connect_timeout(TELEGRAM_CONNECT_TIMEOUT)
        .read_timeout(TELEGRAM_READ_TIMEOUT)
        .write_timeout(TELEGRAM_WRITE_TIMEOUT)
        .pool_timeout(TELEGRAM_POOL_TIMEOUT)
        .get_updates_connect_timeout(TELEGRAM_CONNECT_TIMEOUT)
        .get_updates_read_timeout(TELEGRAM_READ_TIMEOUT)
        .get_updates_write_timeout(TELEGRAM_WRITE_TIMEOUT)
        .get_updates_pool_timeout(TELEGRAM_POOL_TIMEOUT)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("memory", show_memory))
    app.add_handler(CommandHandler("remember", remember))
    app.add_handler(CommandHandler("skills", show_skills))
    app.add_handler(CommandHandler("reset", reset_session))
    app.add_handler(CommandHandler("schedule_in", schedule_in))
    app.add_handler(CommandHandler("tasks", list_tasks))
    app.add_handler(CommandHandler("cancel", cancel_task))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)
    return app


def run_polling_options() -> dict[str, int]:
    """集中提供 Telegram 轮询启动参数。"""

    return {
        "timeout": TELEGRAM_POLLING_TIMEOUT,
        "bootstrap_retries": TELEGRAM_BOOTSTRAP_RETRIES,
    }
