import asyncio
from pathlib import Path

import attr
from litestar import MediaType
from litestar import get
from litestar import post
from litestar.exceptions import HTTPException
from litestar.status_codes import HTTP_201_CREATED
from litestar.status_codes import HTTP_400_BAD_REQUEST
from litestar.status_codes import HTTP_404_NOT_FOUND
from litestar.status_codes import HTTP_409_CONFLICT
from litestar.status_codes import HTTP_500_INTERNAL_SERVER_ERROR

from .java import ensure_java
from .java import is_java_downloaded
from .java import list_downloaded_java_versions
from .java import required_java_version
from .server import MinecraftServer
from .server import PerfStats
from .server import ServerInfo
from .sessions import SessionEntry  # noqa: F401 — re-exported for Litestar schema
from .sessions import WorldSessions
from .sessions import allocate_session_id
from .sessions import list_all_sessions
from .sessions import read_session_log
from .state import AppState
from .worlds import create_world
from .worlds import ensure_version
from .worlds import fetch_available_versions
from .worlds import get_data_version
from .worlds import get_version_string
from .worlds import get_world
from .worlds import list_downloaded_versions
from .worlds import read_jar_data_version
from .worlds import version_jar_path


@attr.s(auto_attribs=True, frozen=True)
class WorldState:
    name: str
    version: str


@attr.s(auto_attribs=True, frozen=True)
class JavaRequirement:
    java_version: int
    downloaded: bool


@attr.s(auto_attribs=True, frozen=True)
class ServerState:
    session_id: int
    version: str
    world: str
    memory_mb: int
    running: bool
    status: str  # "running" | "stopping" | "saving" | "saved"


@attr.s(auto_attribs=True, frozen=True)
class CreateWorldRequest:
    name: str
    version: str


@attr.s(auto_attribs=True, frozen=True)
class StartRequest:
    world: str
    memory_mb: int


@attr.s(auto_attribs=True, frozen=True)
class CommandRequest:
    session_id: int
    command: str


async def _stop_lifecycle(app_state: AppState, server: MinecraftServer) -> None:
    await server.wait_for_exit()
    server.set_status("saving")
    await asyncio.to_thread(server.save_session)
    server.set_status("saved")
    await asyncio.sleep(4)
    try:
        app_state.servers.remove(server)
    except ValueError:
        pass


@get("/", media_type=MediaType.HTML, sync_to_thread=False)
def index() -> str:
    return (Path(__file__).parent / "static" / "index.html").read_text()


@get("/api/versions")
async def api_versions(snapshots: bool = False) -> list[str]:
    try:
        return await fetch_available_versions(include_snapshots=snapshots)
    except RuntimeError as e:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)) from e


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


@get("/api/java/downloaded", sync_to_thread=False)
def api_java_downloaded() -> list[int]:
    return list_downloaded_java_versions()


@get("/api/java/required", sync_to_thread=False)
def api_java_required(mc_version: str) -> JavaRequirement:
    try:
        dv = get_data_version(mc_version)
    except KeyError:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND, detail=f"Unknown Minecraft version: {mc_version!r}"
        ) from None
    try:
        java_ver = required_java_version(dv)
    except ValueError as e:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return JavaRequirement(java_version=java_ver, downloaded=is_java_downloaded(java_ver))


@post("/api/worlds", status_code=HTTP_201_CREATED)
async def api_create_world(data: CreateWorldRequest, app_state: AppState) -> None:
    try:
        await ensure_version(data.version)
        dv = read_jar_data_version(version_jar_path(data.version))
        await ensure_java(required_java_version(dv))
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)) from e
    create_world(data.name, data.version)
    world = get_world(data.name)
    if world is not None:
        app_state.worlds.append(world)
        app_state.worlds.sort(key=lambda w: w.world)


@get("/api/servers", sync_to_thread=False)
def api_servers(app_state: AppState) -> list[ServerState]:
    return [
        ServerState(
            session_id=s.get_session_id(),
            version=s.get_stats().version,
            world=s.get_stats().world,
            memory_mb=s.get_stats().memory_mb,
            running=s.is_running(),
            status=s.get_status(),
        )
        for s in app_state.servers
    ]


@get("/api/status", sync_to_thread=False)
def api_status(app_state: AppState, session_id: int) -> bool:
    for s in app_state.servers:
        if s.get_session_id() == session_id:
            return s.is_running()
    raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"No server with session id {session_id}")


@post("/api/server/start")
async def api_start(data: StartRequest, app_state: AppState) -> bool:
    if any(s.is_running() and s.get_stats().world == data.world for s in app_state.servers):
        raise HTTPException(status_code=HTTP_409_CONFLICT, detail=f"World '{data.world}' is already running")
    world = get_world(data.world)
    if world is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"World '{data.world}' not found")
    try:
        version_str = get_version_string(world.version)
    except KeyError:
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Unknown data version {world.version}"
        ) from None
    server_info = ServerInfo(version=version_str, world=data.world, memory_mb=data.memory_mb)
    new_server = MinecraftServer(server_info, allocate_session_id())
    app_state.servers.append(new_server)
    try:
        await new_server.run()
    except Exception as e:
        app_state.servers.remove(new_server)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)) from e
    return new_server.is_running()


@get("/api/server/logs", sync_to_thread=False)
def api_server_logs(app_state: AppState, session_id: int) -> list[str]:
    for s in app_state.servers:
        if s.get_session_id() == session_id:
            return s.get_output()
    raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"No server with session id {session_id}")


@get("/api/server/stats", sync_to_thread=False)
def api_server_stats(app_state: AppState, session_id: int) -> PerfStats:
    for s in app_state.servers:
        if s.get_session_id() == session_id:
            stats = s.get_perf_stats()
            if stats is None:
                raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"Session {session_id} is not running")
            return stats
    raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"No server with session id {session_id}")


@post("/api/server/command")
async def api_server_command(data: CommandRequest, app_state: AppState) -> None:
    for s in app_state.servers:
        if s.get_session_id() == data.session_id:
            try:
                await s.send_command(data.command)
            except RuntimeError as e:
                raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)) from e
            return
    raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"No server with session id {data.session_id}")


@post("/api/server/stop")
async def api_stop(app_state: AppState, session_id: int) -> None:
    for s in app_state.servers:
        if s.get_session_id() == session_id:
            await s.request_stop()
            asyncio.create_task(_stop_lifecycle(app_state, s))
            return
    raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"No server with session id {session_id}")


@get("/api/sessions", sync_to_thread=False)
def api_sessions() -> list[WorldSessions]:
    return list_all_sessions()


@get("/api/sessions/log", sync_to_thread=False)
def api_session_log(world: str, session_id: int) -> list[str]:
    try:
        return read_session_log(world, session_id)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=str(e)) from e
