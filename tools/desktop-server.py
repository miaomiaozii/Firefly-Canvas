"""Private server-side control for the Windows desktop owner's stdin and ready file."""

import asyncio
import os
import sys
import threading
from pathlib import Path

import uvicorn


class DesktopServer(uvicorn.Server):
    """Publish readiness only after this exact process binds its own listener."""

    def __init__(self, config):
        super().__init__(config)
        self._stop = threading.Event()
        self._ready = Path(os.environ["FIREFLY_DESKTOP_READY_FILE"])

    def run(self, sockets=None):
        # The parent closes its private pipe to request graceful shutdown. There
        # is no HTTP shutdown route and no need to find or signal somebody else's PID.
        def read_stop():
            os.read(sys.stdin.fileno(), 1)
            self._stop.set()

        threading.Thread(target=read_stop, daemon=True, name="desktop-control").start()
        super().run(sockets=sockets)

    async def startup(self, sockets=None):
        if self._stop.is_set():
            self.should_exit = True
            return
        await super().startup(sockets=sockets)
        if self.started and not self._stop.is_set():
            self._ready.touch(exist_ok=False)
        else:
            self.should_exit = True

    async def main_loop(self):
        async def watch_stop():
            while not self._stop.is_set():
                await asyncio.sleep(0.1)
            self.should_exit = True

        watcher = asyncio.create_task(watch_stop())
        try:
            await super().main_loop()
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
