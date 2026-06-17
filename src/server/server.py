import asyncio
import os
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import attr
import psutil

from .java import ensure_java
from .java import required_java_version
from .sessions import write_session_log
from .worlds import read_jar_data_version


def _data_dir() -> Path:
    return Path(os.environ["OPENHOST_APP_DATA_DIR"])


@attr.s(auto_attribs=True, frozen=True)
class ServerInfo:
    version: str
    world: str
    memory_mb: int


@attr.s(auto_attribs=True, frozen=True)
class PerfStats:
    pid: int
    cpu_percent: float
    memory_mb: float
    uptime_seconds: float


class MinecraftServer:
    def __init__(self, server_info: ServerInfo, session_id: int) -> None:
        self._info = server_info
        self._session_id = session_id
        self._process: asyncio.subprocess.Process | None = None
        self._psutil_proc: psutil.Process | None = None
        self._start_time: float | None = None
        self._started_at: datetime | None = None
        self._status: str = "running"
        self._output: deque[str] = deque(maxlen=1000)
        self._reader_task: asyncio.Task[None] | None = None

    async def run(self) -> None:
        world_dir = (_data_dir() / "worlds" / self._info.world).resolve()
        jars = list(world_dir.glob("*.jar"))
        if not jars:
            raise RuntimeError(f"No server JAR found in world '{self._info.world}'")
        jar = jars[0]

        dv = read_jar_data_version(jar)
        java_bin = await ensure_java(required_java_version(dv))

        self._process = await asyncio.create_subprocess_exec(
            str(java_bin),
            f"-Xmx{self._info.memory_mb}M",
            f"-Xms{self._info.memory_mb}M",
            "-jar",
            str(jar),
            "--nogui",
            cwd=world_dir,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._start_time = time.monotonic()
        self._started_at = datetime.now()
        self._status = "running"
        self._psutil_proc = psutil.Process(self._process.pid)
        self._psutil_proc.cpu_percent()  # initialise CPU baseline
        self._reader_task = asyncio.create_task(self._read_output())

    async def _read_output(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        async for line in self._process.stdout:
            self._output.append(line.decode(errors="replace").rstrip())
        await self._process.wait()

    async def request_stop(self) -> None:
        """Send the stop command without waiting for the process to exit."""
        if self._status not in ("running",):
            return
        self._status = "stopping"
        if self._process is not None and self._process.returncode is None and self._process.stdin is not None:
            try:
                self._process.stdin.write(b"stop\n")
                await self._process.stdin.drain()
                self._process.stdin.close()
            except (BrokenPipeError, OSError):
                pass

    async def wait_for_exit(self, timeout: float = 35) -> None:
        """Wait for the process to fully exit after a stop request."""
        if self._process is None:
            return
        try:
            await asyncio.wait_for(self._process.wait(), timeout=timeout)
        except TimeoutError:
            self._process.kill()
            await self._process.wait()
        if self._reader_task is not None and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

    async def stop(self) -> None:
        """Full blocking stop used during app shutdown — does not write a session log."""
        await self.request_stop()
        await self.wait_for_exit()

    def save_session(self) -> None:
        """Write buffered output to the session log directory."""
        if self._started_at is None:
            return
        write_session_log(
            world=self._info.world,
            session_id=self._session_id,
            lines=list(self._output),
            started_at=self._started_at,
        )

    async def send_command(self, command: str) -> None:
        if self._process is None or self._process.returncode is not None:
            raise RuntimeError("Server is not running")
        if self._process.stdin is None:
            raise RuntimeError("Server stdin is not available")
        try:
            self._process.stdin.write(command.encode() + b"\n")
            await self._process.stdin.drain()
        except (BrokenPipeError, OSError) as e:
            raise RuntimeError(f"Failed to send command: {e}") from e
        self._output.append(f"> {command}")

    def get_perf_stats(self) -> PerfStats | None:
        if self._process is None or self._process.returncode is not None:
            return None
        try:
            proc = self._psutil_proc or psutil.Process(self._process.pid)
            return PerfStats(
                pid=self._process.pid,
                cpu_percent=proc.cpu_percent(),
                memory_mb=proc.memory_info().rss / (1024 * 1024),
                uptime_seconds=time.monotonic() - (self._start_time or 0),
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None

    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    def get_status(self) -> str:
        return self._status

    def set_status(self, status: str) -> None:
        self._status = status

    def get_stats(self) -> ServerInfo:
        return self._info

    def get_session_id(self) -> int:
        return self._session_id

    def get_output(self) -> list[str]:
        return list(self._output)
