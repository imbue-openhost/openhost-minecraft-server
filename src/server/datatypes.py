import attr


# wherever possible, the developer version is used
# int rather than a string
@attr.s(auto_attribs=True, frozen=True)
class WorldInfo:
    version: int
    name: str
    port: int = 0


@attr.s(auto_attribs=True, frozen=True)
class JavaRequirement:
    java_version: int
    downloaded: bool


@attr.s(auto_attribs=True, frozen=True)
class ServerState:
    session_id: int
    version: int
    world: str
    port: int
    memory_mb: int
    running: bool
    status: str  # "running" | "stopping" | "saving" | "saved"


@attr.s(auto_attribs=True, frozen=True)
class StartRequest:
    world: str
    memory_mb: int


@attr.s(auto_attribs=True, frozen=True)
class CommandRequest:
    session_id: int
    command: str


@attr.s(auto_attribs=True, frozen=True)
class ServerPerfStats:
    pid: int
    cpu_percent: float
    memory_mb: float
    uptime_seconds: float
