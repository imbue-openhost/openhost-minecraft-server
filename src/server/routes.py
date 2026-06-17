from pathlib import Path

import attr
from litestar import MediaType
from litestar import get
from litestar import post
from litestar.exceptions import HTTPException
from litestar.status_codes import HTTP_201_CREATED
from litestar.status_codes import HTTP_409_CONFLICT
from litestar.status_codes import HTTP_500_INTERNAL_SERVER_ERROR

from .server import MinecraftServer
from .server import ServerInfo
from .state import AppState
from .worlds import create_world
from .worlds import ensure_version
from .worlds import fetch_available_versions
from .worlds import get_version_string
from .worlds import get_world
from .worlds import list_downloaded_versions
from .worlds import read_world_version


@attr.s(auto_attribs=True, frozen=True)
class WorldState:
    name: str
    version: str


@attr.s(auto_attribs=True, frozen=True)
class ServerState:
    id: int
    version: str
    world: str
    memory_mb: int
    running: bool


@attr.s(auto_attribs=True, frozen=True)
class CreateWorldRequest:
    name: str
    version: str


@attr.s(auto_attribs=True, frozen=True)
class StartRequest:
    world: str
    memory_mb: int


@get("/", media_type=MediaType.HTML, sync_to_thread=False)
def index() -> str:
    return (Path(__file__).parent / "static" / "index.html").read_text()


@get("/api/versions")
async def api_versions(snapshots: bool = False) -> list[str]:
    try:
        return await fetch_available_versions(include_snapshots=snapshots)
    except RuntimeError as e:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@get("/api/versions/downloaded", sync_to_thread=False)
def api_downloaded_versions() -> list[str]:
    return list_downloaded_versions()


@get("/api/worlds", sync_to_thread=False)
def api_worlds(app_state: AppState) -> list[WorldState]:
    result = []
    for w in app_state.worlds:
        try:
            version_str = get_version_string(w.version)
        except KeyError:
            version_str = str(w.version)
        result.append(WorldState(name=w.world, version=version_str))
    return result


@post("/api/worlds", status_code=HTTP_201_CREATED)
async def api_create_world(data: CreateWorldRequest, app_state: AppState) -> None:
    try:
        await ensure_version(data.version)
    except RuntimeError as e:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    create_world(data.name, data.version)
    world = get_world(data.name)
    if world is not None:
        app_state.worlds.append(world)
        app_state.worlds.sort(key=lambda w: w.world)


@get("/api/servers", sync_to_thread=False)
def api_servers(app_state: AppState) -> list[ServerState]:
    return [
        ServerState(
            id=s.get_id(),
            version=s.get_stats().version,
            world=s.get_stats().world,
            memory_mb=s.get_stats().memory_mb,
            running=s.is_running(),
        )
        for s in app_state.servers
    ]


@get("/api/status", sync_to_thread=False)
def api_status(app_state: AppState, id_num: int) -> bool:
    for s in app_state.servers:
        if s.get_id() == id_num:
            return s.is_running()
    raise LookupError(f"Tried to get status, but no server found with id {id_num}")


@post("/api/server/start")
async def api_start(data: StartRequest, app_state: AppState) -> bool:
    if any(s.is_running() and s.get_stats().world == data.world for s in app_state.servers):
        raise HTTPException(status_code=HTTP_409_CONFLICT, detail=f"World '{data.world}' is already running")
    version_str = get_version_string(read_world_version(data.world))
    server_info = ServerInfo(version=version_str, world=data.world, memory_mb=data.memory_mb)
    new_server = MinecraftServer(server_info, app_state.current_id)
    app_state.current_id += 1
    app_state.servers.append(new_server)
    await new_server.run()
    return new_server.is_running()


@post("/api/server/stop")
async def api_stop(app_state: AppState, id_num: int) -> bool:
    for s in app_state.servers:
        if s.get_id() == id_num:
            await s.stop()
            return s.is_running()
    raise LookupError(f"Tried to stop, but no server found with id {id_num}")
