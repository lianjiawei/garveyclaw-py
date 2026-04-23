from telegram import Update

from garveyclaw.config import OWNER_ID


def is_owner(update: Update) -> bool:
    # 只允许 owner 触发机器人逻辑，其他用户的消息直接忽略。
    user = update.effective_user
    return bool(user and user.id == OWNER_ID)
