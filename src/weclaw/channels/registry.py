from __future__ import annotations



import asyncio

import logging

import sys

import threading

import time

from dataclasses import dataclass

from typing import Callable, Protocol



from telegram import Bot

from telegram.error import InvalidToken, NetworkError, TelegramError, TimedOut



from weclaw.config import (
    FEISHU_APP_ID,
    FEISHU_APP_SECRET,
    FEISHU_RESTART_DELAY_SECONDS,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_RESTART_DELAY_SECONDS,
    is_effectively_configured,
)
from weclaw.core.delivery import DeliveryRouter

from weclaw.channels.feishu.bot import FeishuBotAdapter, build_event_handler, build_feishu_client

from weclaw.channels.telegram.bot import TelegramMessageSender, build_application, run_polling_options



logger = logging.getLogger(__name__)





def _print_channel_config_error(channel_name: str, summary: str, fix_hint: str) -> None:

    print(f"[{channel_name}] {summary}")

    print(f"[{channel_name}] {fix_hint}")





def _is_feishu_config_error(exc: Exception) -> bool:

    message = str(exc).lower()

    return any(keyword in message for keyword in ["app_id", "app secret", "app_secret", "tenant_access_token", "permission", "auth", "credential"])





class ChannelStarter(Protocol):

    def start(self) -> None: ...





@dataclass(frozen=True, slots=True)

class ChannelRegistration:

    name: str

    channel_key: str

    enabled: Callable[[], bool]

    register_sender: Callable[[DeliveryRouter], None]

    start: Callable[[], ChannelStarter | None]

    run_in_background: bool = False





class TelegramChannelRunner:

    def start(self) -> None:

        while True:

            loop = asyncio.new_event_loop()

            asyncio.set_event_loop(loop)

            try:

                app = build_application()

                app.run_polling(close_loop=False, **run_polling_options())

            except KeyboardInterrupt:

                print("Bot stopped.")

                break

            except InvalidToken:

                _print_channel_config_error(

                    "Telegram",

                    "Bot token is invalid or has expired. Telegram channel will stop now.",

                    "Please update TELEGRAM_BOT_TOKEN in .env, or remove it if you only want to use weclaw-tui / Feishu.",

                )

                logger.error("Telegram bot token was rejected by the server. Stopping Telegram runner until configuration is fixed.")

                break

            except (TimedOut, NetworkError, TelegramError) as exc:

                logger.warning(

                    "Telegram polling failed: %s. Restarting in %s seconds...",

                    exc.__class__.__name__,

                    TELEGRAM_RESTART_DELAY_SECONDS,

                )

                time.sleep(TELEGRAM_RESTART_DELAY_SECONDS)

            except Exception:

                logger.exception(

                    "Bot crashed unexpectedly. Restarting in %s seconds...",

                    TELEGRAM_RESTART_DELAY_SECONDS,

                )

                time.sleep(TELEGRAM_RESTART_DELAY_SECONDS)

            finally:

                asyncio.set_event_loop(None)

                if not loop.is_closed():

                    loop.close()





class FeishuChannelRunner:
    def start(self) -> None:
        while True:
            try:
                import lark_oapi as lark

                lark_error_level = getattr(lark.LogLevel, "ERROR", getattr(lark.LogLevel, "INFO", 1))
                lark_logger = getattr(lark, "logger", None)
                if lark_logger is not None:
                    python_level = int(getattr(lark_error_level, "value", lark_error_level))
                    lark_logger.setLevel(python_level)

                client = build_feishu_client()
                event_handler = build_event_handler(client)
                ws_client = lark.ws.Client(
                    app_id=FEISHU_APP_ID,
                    app_secret=FEISHU_APP_SECRET,
                    event_handler=event_handler,
                    log_level=lark_error_level,
                    auto_reconnect=True,
                )
                print("Feishu bot: WebSocket long connection started.")
                ws_client.start()
                logger.warning(
                    "Feishu WebSocket client exited. Restarting in %s seconds...",
                    FEISHU_RESTART_DELAY_SECONDS,
                )
                time.sleep(FEISHU_RESTART_DELAY_SECONDS)
            except KeyboardInterrupt:
                print("Bot stopped.")
                break
            except Exception as exc:
                if _is_feishu_config_error(exc):
                    _print_channel_config_error(
                        "Feishu",
                        "App credentials are invalid or no longer accepted. Feishu channel will stop now.",
                        "Please update FEISHU_APP_ID / FEISHU_APP_SECRET in .env, or remove them if you only want to use weclaw-tui / Telegram.",
                    )
                    logger.error("Feishu runner stopped because startup failed: %s", exc)
                    break
                logger.warning(
                    "Feishu runner failed: %s. Restarting in %s seconds...",
                    exc,
                    FEISHU_RESTART_DELAY_SECONDS,
                    exc_info=True,
                )
                time.sleep(FEISHU_RESTART_DELAY_SECONDS)




def _has_telegram_config() -> bool:
    return is_effectively_configured(TELEGRAM_BOT_TOKEN)


def _has_feishu_config() -> bool:
    return is_effectively_configured(FEISHU_APP_ID) and is_effectively_configured(FEISHU_APP_SECRET)




def _register_telegram_sender(router: DeliveryRouter) -> None:

    router.register_channel("telegram", TelegramMessageSender(Bot(token=TELEGRAM_BOT_TOKEN)))





def _register_feishu_sender(router: DeliveryRouter) -> None:

    router.register_channel("feishu", FeishuBotAdapter(build_feishu_client()))





def _build_telegram_runner() -> ChannelStarter:

    return TelegramChannelRunner()





def _build_feishu_runner() -> ChannelStarter:

    return FeishuChannelRunner()





def get_registered_channels() -> list[ChannelRegistration]:

    return [

        ChannelRegistration(

            name="Telegram",

            channel_key="telegram",

            enabled=_has_telegram_config,

            register_sender=_register_telegram_sender,

            start=_build_telegram_runner,

            run_in_background=False,

        ),

        ChannelRegistration(

            name="Feishu",

            channel_key="feishu",

            enabled=_has_feishu_config,

            register_sender=_register_feishu_sender,

            start=_build_feishu_runner,

            run_in_background=True,

        ),

    ]





def start_background_channel(name: str, starter: ChannelStarter) -> threading.Thread:

    thread = threading.Thread(target=starter.start, daemon=True, name=f"{name.lower()}-channel")

    thread.start()

    return thread
