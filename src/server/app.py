from typing import cast

import attr
from litestar import Litestar
from litestar import get
from litestar.datastructures import State
from litestar.di import Provide

from .routes import api_create_world
from .routes import api_downloaded_versions
from .routes import api_servers
from .routes import api_start
from .routes import api_status
from .routes import api_stop
from .routes import api_versions
from .routes import api_worlds
from .routes import index
from .state import AppState
from .worlds import VERSION_MAP
from .worlds import fetch_data_versions
from .worlds import load_worlds


def provide_app_state(state: State) -> AppState:
    return cast(AppState, state.app)


async def startup(app: Litestar) -> None:
    VERSION_MAP.update(await fetch_data_versions())
    app_state = AppState()
    app_state.worlds = load_worlds()
    app.state.app = app_state


async def shutdown(app: Litestar) -> None:
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


app = Litestar(
    route_handlers=[health, index, api_versions, api_downloaded_versions, api_worlds, api_create_world, api_servers, api_status, api_start, api_stop],
    dependencies={"app_state": Provide(provide_app_state, sync_to_thread=False)},
    on_startup=[startup],
    on_shutdown=[shutdown],
)
