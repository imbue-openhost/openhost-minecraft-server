import asyncio
import json
import re
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import attr
from litestar import MediaType
from litestar import get
from litestar import post
from litestar.datastructures import UploadFile
from litestar.enums import RequestEncodingType
from litestar.exceptions import HTTPException
from litestar.params import Body
from litestar.response import Response
from litestar.response import ServerSentEvent
from litestar.response.sse import ServerSentEventMessage
from litestar.status_codes import HTTP_201_CREATED
from litestar.status_codes import HTTP_204_NO_CONTENT
from litestar.status_codes import HTTP_400_BAD_REQUEST
from litestar.status_codes import HTTP_404_NOT_FOUND
from litestar.status_codes import HTTP_409_CONFLICT
from litestar.status_codes import HTTP_500_INTERNAL_SERVER_ERROR

from server.datatypes import CommandRequest
from server.datatypes import JavaRequirement
from server.datatypes import ServerPerfStats
from server.datatypes import ServerState
from server.datatypes import StartRequest
from server.datatypes import WorldInfo
from server.datatypes import WorldJarUpdate
from server.java import ensure_java
from server.java import is_java_downloaded
from server.java import list_downloaded_java_versions
from server.java import required_java_version
from server.mod_loaders import cleanup_loader_files
from server.mod_loaders import ensure_loader
from server.mod_loaders import fetch_loader_versions
from server.server import MinecraftServer
from server.sessions import SessionEntry  # noqa: F401 — re-exported for Litestar schema
from server.sessions import WorldSessions
from server.sessions import list_all_sessions
from server.sessions import read_session_log
from server.state import AppState
from server.version_data import VERSION_MAP
from server.worlds import MINECRAFT_PORTS
from server.worlds import assign_world_port
from server.worlds import create_world
from server.worlds import delete_world
from server.worlds import ensure_version
from server.worlds import fetch_available_versions
from server.worlds import get_data_version
from server.worlds import get_version_string
from server.worlds import get_world
from server.worlds import import_world_from_zip
from server.worlds import list_downloaded_versions
from server.worlds import read_jar_data_version
from server.worlds import read_world_config
from server.worlds import save_world_info
from server.worlds import version_jar_path
from server.worlds import world_dir
from server.worlds import write_world_config

_static_index_path: Path = Path(__file__).parent / "static" / "index.html"
_static_app_js_path: Path = Path(__file__).parent / "static" / "app.js"


def _servers_sse_message(app_state: AppState) -> ServerSentEventMessage:
    servers = [
        ServerState(
            session_id=s.get_session_id(),
            version=s.get_version(),
            world=s.get_world(),
            port=s.get_port(),
            memory_mb=s.get_memory_mb(),
            running=s.is_running(),
            status=s.get_status(),
        )
        for s in app_state.servers
    ]
    return ServerSentEventMessage(data=json.dumps([attr.asdict(s) for s in servers]))


async def _stop_lifecycle(app_state: AppState, server: MinecraftServer) -> None:
    await server.wait_for_exit()
    server.set_status("saved")
    app_state.notify_servers_changed()
    await asyncio.sleep(4)
    try:
        app_state.servers.remove(server)
    except ValueError:
        pass
    app_state.notify_servers_changed()


async def _crash_lifecycle(app_state: AppState, server: MinecraftServer) -> None:
    await server.wait_for_process_exit()
    if server.get_status() == "crashed":
        app_state.notify_servers_changed()


@get("/", media_type=MediaType.HTML, sync_to_thread=False)
def index() -> str:
    return _static_index_path.read_text()


@get("/app.js", media_type="application/javascript", sync_to_thread=False)
def app_js() -> str:
    return _static_app_js_path.read_text()


@get("/api/modloader/versions")
async def api_modloader_versions(loader: str, mc_version: str) -> list[str]:
    if loader not in ("forge", "neoforge", "fabric"):
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=f"Unknown loader: {loader!r}")
    try:
        return await fetch_loader_versions(loader, mc_version)
    except RuntimeError as e:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)) from e


@get("/api/versions/map", sync_to_thread=False)
def api_versions_map() -> dict[str, int]:
    return {v: k for k, v in VERSION_MAP.items()}


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
def api_worlds(app_state: AppState) -> list[WorldInfo]:
    return app_state.worlds


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
async def api_create_world(data: WorldInfo, app_state: AppState) -> None:
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_\- ]*$", data.name):
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="World name must start with a letter or digit and contain only letters, digits, spaces, hyphens, or underscores",
        )
    try:
        await ensure_version(data.version)
        dv = read_jar_data_version(version_jar_path(data.version))
        java_bin = await ensure_java(required_java_version(dv))
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)) from e
    used_ports = {w.port for w in app_state.worlds}
    if data.port != 0:
        if data.port not in MINECRAFT_PORTS or data.port in used_ports:
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail=f"Port {data.port} is not available",
            )
        port = data.port
    else:
        try:
            port = assign_world_port(used_ports)
        except RuntimeError as e:
            raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)) from e
    create_world(attr.evolve(data, port=port))
    if data.mod_loader != "vanilla":
        mc_version = get_version_string(data.version)
        try:
            await ensure_loader(world_dir(data.name), mc_version, data.mod_loader, data.mod_loader_version, java_bin)
        except (RuntimeError, ValueError) as e:
            delete_world(data.name)
            raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)) from e
    world = get_world(data.name)
    if world is not None:
        app_state.worlds.append(world)
        app_state.worlds.sort(key=lambda w: w.name)


@get("/api/servers", sync_to_thread=False)
def api_servers(app_state: AppState) -> list[ServerState]:
    return [
        ServerState(
            session_id=s.get_session_id(),
            version=s.get_version(),
            world=s.get_world(),
            port=s.get_port(),
            memory_mb=s.get_memory_mb(),
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
    if any(s.is_running() and s.get_world() == data.world for s in app_state.servers):
        raise HTTPException(status_code=HTTP_409_CONFLICT, detail=f"World '{data.world}' is already running")
    world = get_world(data.world)
    if world is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"World '{data.world}' not found")

    try:
        new_server = MinecraftServer(data)
    except Exception as e:
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=f"server startup failed for '{data.world} failed"
        ) from e

    app_state.servers.append(new_server)
    try:
        await new_server.run()
    except Exception as e:
        app_state.servers.remove(new_server)
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)) from e
    app_state.notify_servers_changed()
    asyncio.create_task(_crash_lifecycle(app_state, new_server))
    return new_server.is_running()


@get("/api/server/logs", sync_to_thread=False)
def api_server_logs(app_state: AppState, session_id: int) -> list[str]:
    for s in app_state.servers:
        if s.get_session_id() == session_id:
            try:
                return read_session_log(s.get_world(), session_id)
            except (FileNotFoundError, ValueError):
                return s.get_output()
    raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"No server with session id {session_id}")


@get("/api/server/stats", sync_to_thread=False)
def api_server_stats(app_state: AppState, session_id: int) -> ServerPerfStats | Response[None]:
    for s in app_state.servers:
        if s.get_session_id() == session_id:
            stats = s.get_perf_stats()
            if stats is None:
                return Response(content=None, status_code=HTTP_204_NO_CONTENT)
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
            app_state.notify_servers_changed()
            asyncio.create_task(_stop_lifecycle(app_state, s))
            return
    raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"No server with session id {session_id}")


@post("/api/server/dismiss")
async def api_dismiss(app_state: AppState, session_id: int) -> None:
    for s in app_state.servers:
        if s.get_session_id() == session_id:
            if s.get_status() != "crashed":
                raise HTTPException(status_code=HTTP_409_CONFLICT, detail=f"Server {session_id} is not crashed")
            app_state.servers.remove(s)
            app_state.notify_servers_changed()
            return
    raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"No server with session id {session_id}")


@dataclass
class _WorldImportForm:
    file: UploadFile
    name: str
    version: int
    port: int = 0


@post("/api/worlds/import", status_code=HTTP_201_CREATED, request_max_body_size=None)
async def api_import_world(
    data: Annotated[_WorldImportForm, Body(media_type=RequestEncodingType.MULTI_PART)],
    app_state: AppState,
) -> None:
    name = data.name.strip()
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_\- ]*$", name):
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="World name must start with a letter or digit and contain only letters, digits, spaces, hyphens, or underscores",
        )
    if any(w.name == name for w in app_state.worlds):
        raise HTTPException(status_code=HTTP_409_CONFLICT, detail=f"World '{name}' already exists")
    used_ports = {w.port for w in app_state.worlds}
    if data.port != 0:
        if data.port not in MINECRAFT_PORTS or data.port in used_ports:
            raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=f"Port {data.port} is not available")
        port = data.port
    else:
        try:
            port = assign_world_port(used_ports)
        except RuntimeError as e:
            raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)) from e
    try:
        zip_bytes = await data.file.read()
        import_world_from_zip(zip_bytes, name, port, data.version)
    except ValueError as e:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)) from e
    world = get_world(name)
    if world is not None:
        app_state.worlds.append(world)
        app_state.worlds.sort(key=lambda w: w.name)


@get("/api/worlds/{name:str}/config", sync_to_thread=False)
def api_world_config(name: str) -> dict[str, str]:
    if get_world(name) is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"World '{name}' not found")
    return read_world_config(name)


@post("/api/worlds/{name:str}/config", status_code=HTTP_204_NO_CONTENT)
async def api_update_world_config(name: str, data: dict[str, str]) -> None:
    if get_world(name) is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"World '{name}' not found")
    try:
        write_world_config(name, data)
    except OSError as e:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)) from e


@post("/api/worlds/{name:str}/jar", status_code=HTTP_204_NO_CONTENT)
async def api_update_world_jar(name: str, data: WorldJarUpdate, app_state: AppState) -> None:
    world = get_world(name)
    if world is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"World '{name}' not found")
    if any(s.is_running() and s.get_world() == name for s in app_state.servers):
        raise HTTPException(status_code=HTTP_409_CONFLICT, detail=f"World '{name}' is currently running")
    try:
        await ensure_version(data.version)
        dv = read_jar_data_version(version_jar_path(data.version))
        java_bin = await ensure_java(required_java_version(dv))
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)) from e
    wdir = world_dir(name)
    version_changed = data.version != world.version
    loader_changed = data.mod_loader != world.mod_loader or data.mod_loader_version != world.mod_loader_version
    if (version_changed or loader_changed) and world.mod_loader != "vanilla":
        cleanup_loader_files(wdir, world.mod_loader)
    if data.mod_loader != "vanilla":
        mc_version = get_version_string(data.version)
        try:
            await ensure_loader(wdir, mc_version, data.mod_loader, data.mod_loader_version, java_bin)
        except (RuntimeError, ValueError) as e:
            raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)) from e
        (wdir / "mods").mkdir(exist_ok=True)
    save_world_info(name, world.port, data.version, data.mod_loader, data.mod_loader_version)
    for i, w in enumerate(app_state.worlds):
        if w.name == name:
            app_state.worlds[i] = attr.evolve(
                w, version=data.version, mod_loader=data.mod_loader, mod_loader_version=data.mod_loader_version
            )
            break


@get("/api/servers/events")
async def api_server_events(app_state: AppState) -> ServerSentEvent:
    async def gen() -> AsyncGenerator[ServerSentEventMessage, None]:
        queue: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        app_state._sse_queues.append(queue)
        try:
            yield _servers_sse_message(app_state)
            while True:
                try:
                    await asyncio.wait_for(queue.get(), timeout=25)
                    yield _servers_sse_message(app_state)
                except TimeoutError:
                    yield ServerSentEventMessage(comment="keepalive")
        finally:
            try:
                app_state._sse_queues.remove(queue)
            except ValueError:
                pass

    return ServerSentEvent(content=gen())


@get("/api/sessions", sync_to_thread=False)
def api_sessions() -> list[WorldSessions]:
    return list_all_sessions()


@get("/api/sessions/log", sync_to_thread=False)
def api_session_log(world: str, session_id: int) -> list[str]:
    try:
        return read_session_log(world, session_id)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=str(e)) from e
