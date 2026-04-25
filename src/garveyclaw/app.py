import logging

from telegram.error import TimedOut

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

    app = build_application()
    print("Bot is running...")
    try:
        app.run_polling(**run_polling_options())
    except KeyboardInterrupt:
        print("Bot stopped.")
    except TimedOut:
        logger.warning("Bot startup timed out while connecting to Telegram. Please check network or proxy settings.")
    except Exception as exc:
        logger.warning("Bot stopped because startup failed: %s", exc.__class__.__name__)


if __name__ == "__main__":
    main()
