#!/usr/bin/env python3
"""Точка входа для Render Web Service (README pixel + install ping)."""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from aiohttp import web
from dotenv import load_dotenv

from ping_http import create_ping_app

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("ping_server")


async def run_server() -> None:
    port = int(os.getenv("PORT", "8787"))
    runner = web.AppRunner(create_ping_app())
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("Ping server listening on 0.0.0.0:%s", port)

    stop = asyncio.Event()

    def _stop(*_: object) -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            pass

    await stop.wait()
    await runner.cleanup()


def main() -> None:
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
