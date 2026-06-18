from typing import Any
from typing import cast

import attr
from litestar import Litestar
from litestar import Request
from litestar import get
from litestar.datastructures import State
from litestar.di import Provide
from litestar.enums import ScopeType
from litestar.exceptions import HTTPException
from litestar.response import Response
from litestar.status_codes import HTTP_500_INTERNAL_SERVER_ERROR
from litestar.types import ASGIApp
from litestar.types import Message
from litestar.types import Receive
from litestar.types import Scope
from litestar.types import Send
from loguru import logger

from server.routes import api_create_world
from server.routes import api_downloaded_versions
from server.routes import api_java_downloaded
from server.routes import api_java_required
from server.routes import api_server_command
from server.routes import api_server_logs
from server.routes import api_server_stats
from server.routes import api_servers
from server.routes import api_session_log
from server.routes import api_sessions
from server.routes import api_start
from server.routes import api_status
from server.routes import api_stop
from server.routes import api_versions
from server.routes import api_versions_map
from server.routes import api_worlds
from server.routes import app_js
from server.routes import index
from server.state import AppState
from server.worlds import load_worlds


def provide_app_state(state: State) -> AppState:
    return cast(AppState, state.app)


async def startup(app: Litestar) -> None:
    app_state = AppState()
    app_state.worlds = load_worlds()
    app.state.app = app_state


async def shutdown(app: Litestar) -> None:
    if not hasattr(app.state, "app"):
        return
    app_state = cast(AppState, app.state.app)
    for s in app_state.servers:
        if s.is_running():
            await s.stop()


@attr.s(auto_attribs=True, frozen=True)
class HealthStatus:
    status: str


@get("/health", sync_to_thread=False)
def health() -> HealthStatus:
    return HealthStatus(status="ok")


class _AccessLog:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != ScopeType.HTTP:
            await self.app(scope, receive, send)
            return

        status: int = 0

        async def _capture(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        await self.app(scope, receive, _capture)
        logger.info("{} {} {}", scope["method"], scope["path"], status)


def _unhandled_exception_handler(request: Request[Any, Any, Any], exc: Exception) -> Response[dict[str, str]]:
    if isinstance(exc, HTTPException):
        return Response({"detail": exc.detail}, status_code=exc.status_code)
    logger.exception("Unhandled exception on {} {}", request.method, request.url)
    detail = str(exc) or type(exc).__name__
    return Response({"detail": detail}, status_code=HTTP_500_INTERNAL_SERVER_ERROR)


app = Litestar(
    route_handlers=[
        health,
        index,
        app_js,
        api_versions,
        api_versions_map,
        api_downloaded_versions,
        api_worlds,
        api_create_world,
        api_java_downloaded,
        api_java_required,
        api_server_logs,
        api_server_stats,
        api_server_command,
        api_servers,
        api_sessions,
        api_session_log,
        api_status,
        api_start,
        api_stop,
    ],
    middleware=[_AccessLog],
    dependencies={"app_state": Provide(provide_app_state, sync_to_thread=False)},
    on_startup=[startup],
    on_shutdown=[shutdown],
    exception_handlers={Exception: _unhandled_exception_handler},
)
