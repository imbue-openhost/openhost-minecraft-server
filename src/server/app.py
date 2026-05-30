import attr
from litestar import Litestar
from litestar import MediaType
from litestar import get


@attr.s(auto_attribs=True, frozen=True)
class HealthStatus:
    status: str


@get("/health", sync_to_thread=False)
def health() -> HealthStatus:
    return HealthStatus(status="ok")


@get("/", media_type=MediaType.HTML, sync_to_thread=False)
def index() -> str:
    return (
        "<!doctype html>"
        "<html lang='en'>"
        "<head><meta charset='utf-8'><title>app-template</title></head>"
        "<body><h1>app-template</h1><p>Replace me with your app.</p></body>"
        "</html>"
    )


app = Litestar(route_handlers=[health, index])
