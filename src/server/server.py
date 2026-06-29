import asyncio
import os
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import TextIO

import psutil
from loguru import logger

from server.datatypes import ServerPerfStats
from server.datatypes import StartRequest
from server.java import ensure_java
from server.java import required_java_version
from server.mod_loaders import ensure_loader
from server.mod_loaders import get_launch_cmd
from server.sessions import allocate_session_id
from server.sessions import session_log_path
from server.worlds import ensure_version
from server.worlds import get_version
from server.worlds import get_version_string
from server.worlds import get_world_loader_info
from server.worlds import get_world_port
from server.worlds import version_jar_path


def _write_server_port(world_dir: Path, port: int) -> None:
    props = world_dir / "server.properties"
    if props.exists():
        lines = props.read_text().splitlines()
        new_lines: list[str] = []
        updated = False
        for line in lines:
            if line.startswith("server-port="):
                new_lines.append(f"server-port={port}")
                updated = True
            else:
                new_lines.append(line)
        if not updated:
            new_lines.append(f"server-port={port}")
        props.write_text("\n".join(new_lines) + "\n")
    else:
        props.write_text(f"server-port={port}\n")


def _data_dir() -> Path:
    return Path(os.environ["OPENHOST_APP_DATA_DIR"])


class MinecraftServer:
    def __init__(self, start_req: StartRequest) -> None:
        self._world: str = start_req.world
        self._memory_mb: int = start_req.memory_mb
        self._session_id: int = allocate_session_id()
        self._version = get_version(start_req.world)
        self._port: int = get_world_port(start_req.world)
        self._mod_loader, self._loader_version = get_world_loader_info(start_req.world)
        self._process: asyncio.subprocess.Process | None = None
        self._psutil_proc: psutil.Process | None = None
        self._start_time: float | None = None
        self._started_at: datetime | None = None
        self._status: str = "running"
        self._output: deque[str] = deque(maxlen=1000)
        self._log_file: TextIO | None = None
        self._reader_task: asyncio.Task[None] | None = None

    async def run(self) -> None:
        world_dir = (_data_dir() / "worlds" / self._world).resolve()

        await ensure_version(self._version)
        java_bin = await ensure_java(required_java_version(self._version))

        _write_server_port(world_dir, self._port)

        if self._mod_loader == "vanilla":
            jar = version_jar_path(self._version).resolve()
            cmd: list[str] = [
                str(java_bin),
                f"-Xmx{self._memory_mb}M",
                f"-Xms{self._memory_mb}M",
                "-jar",
                str(jar),
                "--nogui",
            ]
            proc_env = None
        else:
            mc_version = get_version_string(self._version)
            await ensure_loader(world_dir, mc_version, self._mod_loader, self._loader_version, java_bin)
            cmd, extra_env = get_launch_cmd(
                world_dir, mc_version, self._mod_loader, self._loader_version, self._memory_mb, java_bin
            )
            proc_env = {**os.environ, **extra_env} if extra_env else None

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=world_dir,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=proc_env,
        )
        self._start_time = time.monotonic()
        self._started_at = datetime.now()
        self._status = "running"
        self._psutil_proc = psutil.Process(self._process.pid)
        self._psutil_proc.cpu_percent()  # initialise CPU baseline
        self._log_file = session_log_path(self._world, self._session_id, self._started_at).open("w", encoding="utf-8")
        self._reader_task = asyncio.create_task(self._read_output())

    async def _read_output(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        async for line in self._process.stdout:
            decoded = line.decode(errors="replace").rstrip()
            self._output.append(decoded)
            if self._log_file is not None:
                self._log_file.write(decoded + "\n")
                self._log_file.flush()
        await self._process.wait()
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None
        if self._status == "running":
            self._status = "crashed"
            if self._process.stdin is not None and not self._process.stdin.is_closing():
                self._process.stdin.close()
                try:
                    await self._process.stdin.wait_closed()
                except Exception:
                    pass
            logger.error(
                "Minecraft server for world {!r} exited unexpectedly with code {}",
                self._world,
                self._process.returncode,
            )

    async def wait_for_process_exit(self) -> None:
        if self._reader_task is not None:
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

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
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None

    async def stop(self) -> None:
        """Full blocking stop used during app shutdown — does not write a session log."""
        await self.request_stop()
        await self.wait_for_exit()

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

    def get_perf_stats(self) -> ServerPerfStats | None:
        if self._process is None or self._process.returncode is not None:
            return None
        try:
            proc = self._psutil_proc or psutil.Process(self._process.pid)
            return ServerPerfStats(
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

    def get_world(self) -> str:
        return self._world

    def get_version(self) -> int:
        return self._version

    def get_memory_mb(self) -> int:
        return self._memory_mb

    def get_session_id(self) -> int:
        return self._session_id

    def get_port(self) -> int:
        return self._port

    def get_output(self) -> list[str]:
        return list(self._output)
