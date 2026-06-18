from server.datatypes import WorldInfo
from server.server import MinecraftServer


class AppState:
    def __init__(self) -> None:
        self.worlds: list[WorldInfo] = []
        self.servers: list[MinecraftServer] = []
