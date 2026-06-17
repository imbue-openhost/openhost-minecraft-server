from typing import Any
from typing import cast

import attr
from litestar import Litestar
from litestar import Request
from litestar import get
from litestar.datastructures import State
from litestar.di import Provide
from litestar.response import Response
from litestar.status_codes import HTTP_500_INTERNAL_SERVER_ERROR
from loguru import logger

from .routes import api_create_world
from .routes import api_downloaded_versions
from .routes import api_java_downloaded
from .routes import api_java_required
from .routes import api_server_command
from .routes import api_server_logs
from .routes import api_server_stats
from .routes import api_servers
from .routes import api_session_log
from .routes import api_sessions
from .routes import api_start
from .routes import api_status
from .routes import api_stop
from .routes import api_versions
from .routes import api_worlds
from .routes import index
from .state import AppState
from .worlds import load_worlds


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


def _unhandled_exception_handler(request: Request[Any, Any, Any], exc: Exception) -> Response[dict[str, str]]:
    logger.exception("Unhandled exception on {} {}", request.method, request.url)
    detail = str(exc) or type(exc).__name__
    return Response({"detail": detail}, status_code=HTTP_500_INTERNAL_SERVER_ERROR)


app = Litestar(
    route_handlers=[
        health,
        index,
        api_versions,
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
    dependencies={"app_state": Provide(provide_app_state, sync_to_thread=False)},
    on_startup=[startup],
    on_shutdown=[shutdown],
    exception_handlers={Exception: _unhandled_exception_handler},
)
