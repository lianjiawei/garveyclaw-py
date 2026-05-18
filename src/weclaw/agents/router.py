from __future__ import annotations



import logging

from typing import TYPE_CHECKING, Any



from weclaw.config import AGENT_PROVIDER

from weclaw.core.provider_state import get_provider

from weclaw.core.delivery import MessageSender

from weclaw.decision.models import DecisionPlan

from weclaw.core.response import AgentReply

from weclaw.core.types import ConversationRef



if TYPE_CHECKING:

    from weclaw.channels.feishu.bot import FeishuIncomingMessage

    from telegram import Update



logger = logging.getLogger(__name__)





class AgentServiceError(Exception):

    """统一表示当前 Agent Provider 调用失败。"""





def normalize_provider_name() -> str:

    return get_provider()





def build_telegram_session_scope(update: "Update") -> str:

    if not update.effective_chat:

        raise AgentServiceError("Missing Telegram chat context.")

    return f"telegram:chat:{update.effective_chat.id}"





def build_telegram_conversation(update: "Update") -> ConversationRef:

    if not update.effective_chat:

        raise AgentServiceError("Missing Telegram chat context.")

    return ConversationRef(

        channel="telegram",

        target_id=str(update.effective_chat.id),

        session_scope=build_telegram_session_scope(update),

        user_id=str(update.effective_user.id) if update.effective_user else None,

    )





def build_feishu_conversation(incoming: "FeishuIncomingMessage", scope: str) -> ConversationRef:

    return ConversationRef(

        channel="feishu",

        target_id=incoming.chat_id,

        session_scope=scope,

        user_id=incoming.sender_open_id or None,

    )


def build_weixin_conversation(target_id: str, sender_id: str, chat_type: str = "dm") -> ConversationRef:
    scope_kind = "group" if chat_type == "group" else "dm"
    return ConversationRef(
        channel="weixin",
        target_id=target_id,
        session_scope=f"weixin:{scope_kind}:{target_id}",
        user_id=sender_id or None,
    )





def build_tui_conversation(instance_scope: str) -> ConversationRef:

    return ConversationRef(

        channel="tui",

        target_id=instance_scope,

        session_scope=instance_scope,

    )





async def run_agent(

    prompt: str,

    sender: MessageSender,

    target_id: str | int,

    continue_session: bool,

    record_text: str | None = None,

    uploaded_image: Any | None = None,

    uploaded_file: Any | None = None,

    session_scope: str | None = None,

    channel: str | None = None,

    decision_plan: DecisionPlan | None = None,

) -> AgentReply:

    """统一 Agent 调用入口，后续可以继续扩展更多 Provider。"""



    provider = normalize_provider_name()



    # 自动切换策略（最小版）：

    # 当前默认 Provider 是 Claude 时，如果本轮明显是图片生成/改图请求，

    # 则只对这一轮临时路由到 OpenAI，不修改全局 provider 状态。

    if provider == "claude":

        try:

            from weclaw.agents.openai import wants_image_output



            if wants_image_output(prompt, record_text, uploaded_image):

                provider = "openai"

        except Exception:

            logger.exception("Auto-switch strategy check failed")



    try:

        if provider == "claude":

            from weclaw.agents.claude import run_agent as run_claude_agent



            text = await run_claude_agent(

                prompt=prompt,

                sender=sender,

                target_id=target_id,

                continue_session=continue_session,

                record_text=record_text,

                uploaded_image=uploaded_image,

                uploaded_file=uploaded_file,

                session_scope=session_scope,

                channel=channel,

                decision_plan=decision_plan,

            )

            return AgentReply.from_text(text, provider=provider)



        if provider == "openai":

            from weclaw.agents.openai import run_openai_agent



            reply = await run_openai_agent(

                prompt=prompt,

                sender=sender,

                target_id=target_id,

                continue_session=continue_session,

                record_text=record_text,

                uploaded_image=uploaded_image,

                uploaded_file=uploaded_file,

                session_scope=session_scope,

                channel=channel,

                decision_plan=decision_plan,

            )

            reply.provider = provider

            return reply



        raise AgentServiceError(f"Unsupported AGENT_PROVIDER: {provider}")

    except AgentServiceError:

        raise

    except Exception as exc:

        logger.exception("Agent provider request failed: %s", provider)

        raise AgentServiceError(str(exc) or "Failed to get response from agent provider.") from exc
