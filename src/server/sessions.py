import os
import re
from datetime import datetime
from pathlib import Path

import attr

_FILENAME_RE = re.compile(r"^(\d{6})-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})\.log$")


def _data_dir() -> Path:
    return Path(os.environ["OPENHOST_APP_DATA_DIR"])


def allocate_session_id() -> int:
    """Read, increment, and persist the global session counter. Returns the new ID."""
    path = _data_dir() / "session_counter"
    try:
        current = int(path.read_text().strip())
    except (FileNotFoundError, ValueError):
        current = 0
    next_id = current + 1
    path.write_text(str(next_id))
    return next_id


def _log_dir(world: str) -> Path:
    return _data_dir() / "logs" / world


def write_session_log(world: str, session_id: int, lines: list[str], started_at: datetime) -> None:
    d = _log_dir(world)
    d.mkdir(parents=True, exist_ok=True)
    ts = started_at.strftime("%Y-%m-%dT%H-%M-%S")
    (d / f"{session_id:06d}-{ts}.log").write_text("\n".join(lines))


@attr.s(auto_attribs=True, frozen=True)
class SessionEntry:
    session_id: int
    started_at: str  # e.g. "2025-06-17T14-30-00"


@attr.s(auto_attribs=True, frozen=True)
class WorldSessions:
    world: str
    sessions: list[SessionEntry]


def list_all_sessions() -> list[WorldSessions]:
    logs_dir = _data_dir() / "logs"
    if not logs_dir.exists():
        return []
    result = []
    for world_dir in sorted(logs_dir.iterdir()):
        if not world_dir.is_dir():
            continue
        entries = []
        for f in sorted(world_dir.glob("*.log"), reverse=True):
            m = _FILENAME_RE.match(f.name)
            if m:
                entries.append(SessionEntry(session_id=int(m.group(1)), started_at=m.group(2)))
        if entries:
            result.append(WorldSessions(world=world_dir.name, sessions=entries))
    return result


def read_session_log(world: str, session_id: int) -> list[str]:
    if "/" in world or ".." in world:
        raise ValueError(f"Invalid world name: {world!r}")
    matches = list(_log_dir(world).glob(f"{session_id:06d}-*.log"))
    if not matches:
        raise FileNotFoundError(f"Session {session_id} not found for world {world!r}")
    return matches[0].read_text().splitlines()
