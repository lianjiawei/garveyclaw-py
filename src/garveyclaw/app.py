import logging
import time

from telegram.error import NetworkError, TelegramError, TimedOut

from garveyclaw.config import TELEGRAM_RESTART_DELAY_SECONDS
from garveyclaw.telegram_bot import build_application, run_polling_options

logger = logging.getLogger(__name__)


def main() -> None:
    """程序入口：初始化日志后启动 Telegram 轮询。"""

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    # 压低第三方库的高频日志，避免正常轮询时刷屏。
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext._utils.networkloop").setLevel(logging.CRITICAL)
    logging.getLogger("telegram.ext._updater").setLevel(logging.CRITICAL)

    print("Bot is running...")
    while True:
        try:
            # 每次重连都重新构建 Application，避免复用已经关闭的事件循环或调度器。
            app = build_application()
            app.run_polling(**run_polling_options())
        except KeyboardInterrupt:
            print("Bot stopped.")
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


if __name__ == "__main__":
    main()
