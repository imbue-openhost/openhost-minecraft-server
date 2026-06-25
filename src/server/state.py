import asyncio

from server.datatypes import WorldInfo
from server.server import MinecraftServer


class AppState:
    def __init__(self) -> None:
        self.worlds: list[WorldInfo] = []
        self.servers: list[MinecraftServer] = []
        self._sse_queues: list[asyncio.Queue[None]] = []

    def notify_servers_changed(self) -> None:
        for q in self._sse_queues:
            if not q.full():
                q.put_nowait(None)
