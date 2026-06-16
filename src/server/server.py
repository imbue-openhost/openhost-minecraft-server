import attr


@attr.s(auto_attribs=True, frozen=True)
class ServerInfo:
    version: str
    world: str
    memory_mb: int


class MinecraftServer:
    def __init__(self, server_info: ServerInfo, id_num: int) -> None:
        self._running: bool = False
        self._info: ServerInfo = server_info
        self._id: int = id_num

    async def run(self) -> None:
        print(self._info, "starting")
        self._running = True

    async def stop(self) -> None:
        print(self._info, "stopping")
        self._running = False

    def is_running(self) -> bool:
        return self._running

    def get_stats(self) -> ServerInfo:
        return self._info

    def get_id(self) -> int:
        return self._id
