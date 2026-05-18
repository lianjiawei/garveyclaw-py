from __future__ import annotations



import asyncio

import logging

import threading

from dataclasses import dataclass

from typing import Any



from weclaw.core.delivery import DeliveryRouter

from weclaw.tasks.scheduler import setup_scheduler



logger = logging.getLogger(__name__)





@dataclass(slots=True)

class BackgroundSchedulerRuntime:

    scheduler: Any

    loop: asyncio.AbstractEventLoop

    thread: threading.Thread

    _stopped: threading.Event





def start_background_scheduler(router: DeliveryRouter) -> BackgroundSchedulerRuntime:

    # App mode owns a dedicated scheduler loop thread so channel event loops stay independent.

    ready = threading.Event()

    stopped = threading.Event()

    state: dict[str, object] = {}

    startup_error: list[BaseException] = []



    def run_scheduler_loop() -> None:

        loop = asyncio.new_event_loop()

        asyncio.set_event_loop(loop)

        try:

            scheduler = setup_scheduler(router, event_loop=loop)

            scheduler.start()

        except BaseException as exc:

            startup_error.append(exc)

            logger.exception("Scheduler startup failed")

            ready.set()

            loop.close()

            return



        async def _watch_stop() -> None:

            while not stopped.is_set():

                await asyncio.sleep(0.3)

            loop.stop()



        loop.create_task(_watch_stop())



        state["loop"] = loop

        state["scheduler"] = scheduler

        ready.set()

        logger.info("Scheduler loop started in background thread")

        loop.run_forever()

        logger.info("Scheduler loop stopping")

        scheduler.shutdown(wait=False)

        loop.close()



    thread = threading.Thread(target=run_scheduler_loop, daemon=True, name="weclaw-scheduler")

    thread.start()

    ready.wait()

    if startup_error:

        raise RuntimeError("Failed to start background scheduler.") from startup_error[0]

    logger.info("Scheduler runtime ready")

    return BackgroundSchedulerRuntime(

        scheduler=state["scheduler"],

        loop=state["loop"],

        thread=thread,

        _stopped=stopped,

    )





def stop_background_scheduler(runtime: BackgroundSchedulerRuntime) -> None:

    runtime._stopped.set()

    runtime.thread.join(timeout=3)

    if runtime.thread.is_alive():

        logger.warning("Scheduler thread did not exit promptly, continuing shutdown")
