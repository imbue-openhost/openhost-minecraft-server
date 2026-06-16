from .server import MinecraftServer
from .worlds import WorldInfo


class AppState:
    def __init__(self) -> None:
        self.worlds: list[WorldInfo] = []
        self.servers: list[MinecraftServer] = []
        self.current_id: int = 0
