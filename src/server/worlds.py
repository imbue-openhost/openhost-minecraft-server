import contextlib
import hashlib
import json
import os
import sqlite3
import zipfile
from collections.abc import Iterator
from pathlib import Path

import httpx

from server.datatypes import WorldInfo
from server.version_data import VERSION_MAP

_MANIFEST_URL = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"
MINECRAFT_PORTS: tuple[int, ...] = tuple(range(25565, 25570))


def _data_dir() -> Path:
    return Path(os.environ["OPENHOST_APP_DATA_DIR"])


def _temp_dir() -> Path:
    return Path(os.environ["OPENHOST_APP_TEMP_DIR"])


@contextlib.contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    path = Path(os.environ["OPENHOST_SQLITE_DEFAULT"])
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS worlds (
            name    TEXT PRIMARY KEY,
            port    INTEGER NOT NULL,
            version INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.commit()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
    finally:
        conn.close()


def get_world_port(name: str) -> int:
    with _db() as conn:
        row = conn.execute("SELECT port FROM worlds WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise KeyError(f"No port assigned for world {name!r}")
    return int(row[0])


def save_world_port(name: str, port: int) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT INTO worlds (name, port, version) VALUES (?, ?, 0) "
            "ON CONFLICT(name) DO UPDATE SET port = excluded.port",
            (name, port),
        )


def save_world_info(name: str, port: int, version: int) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT INTO worlds (name, port, version) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET port = excluded.port, version = excluded.version",
            (name, port, version),
        )


def assign_world_port(used_ports: set[int]) -> int:
    for port in MINECRAFT_PORTS:
        if port not in used_ports:
            return port
    raise RuntimeError(f"No Minecraft ports available (pool exhausted: {MINECRAFT_PORTS[0]}-{MINECRAFT_PORTS[-1]})")


def get_version_string(version: int) -> str:
    return VERSION_MAP[version]


def get_data_version(version_str: str) -> int:
    for dv, vs in VERSION_MAP.items():
        if vs == version_str:
            return dv
    raise KeyError(f"Unknown version: {version_str}")


def read_jar_data_version(jar_path: Path) -> int:
    with zipfile.ZipFile(jar_path) as zf:
        with zf.open("version.json") as f:
            data = json.loads(f.read())
    world_version = data["world_version"]
    if not isinstance(world_version, int):
        raise ValueError(f"version.json world_version: expected int, got {type(world_version).__name__}")
    return world_version


def get_version(name: str) -> int:
    with _db() as conn:
        row = conn.execute("SELECT version FROM worlds WHERE name = ?", (name,)).fetchone()
    if row is not None and int(row[0]) != 0:
        return int(row[0])
    # Fallback: read from a JAR left in the world dir (pre-migration worlds).
    d = _data_dir() / "worlds" / name
    jars = list(d.glob("*.jar"))
    if not jars:
        raise LookupError(f"No version recorded and no JAR found for world {name!r}")
    return read_jar_data_version(jars[0])


def get_world(name: str) -> WorldInfo | None:
    if not (_data_dir() / "worlds" / name).is_dir():
        return None
    with _db() as conn:
        row = conn.execute("SELECT port, version FROM worlds WHERE name = ?", (name,)).fetchone()
    if row is None:
        return None
    port, version = int(row[0]), int(row[1])
    if version == 0:
        try:
            version = get_version(name)
        except LookupError:
            return None
    return WorldInfo(version=version, name=name, port=port)


def load_worlds() -> list[WorldInfo]:
    d = _data_dir() / "worlds"
    if not d.exists():
        return []
    result = []
    for p in sorted(d.iterdir()):
        if p.is_dir():
            world = get_world(p.name)
            if world is not None:
                result.append(world)
    return result


def create_world(info: WorldInfo) -> None:
    d = _data_dir() / "worlds" / info.name
    d.mkdir(parents=True, exist_ok=True)
    (d / "eula.txt").write_text("eula=true\n")
    save_world_info(info.name, info.port, info.version)


def version_jar_path(version: int) -> Path:
    return _temp_dir() / "versions" / f"{get_version_string(version)}.jar"


def list_downloaded_versions() -> list[str]:
    d = _temp_dir() / "versions"
    if not d.exists():
        return []
    return sorted(p.stem for p in d.iterdir() if p.suffix == ".jar")


async def download_version(version: int) -> None:
    version_str = get_version_string(version)
    try:
        async with httpx.AsyncClient() as client:
            manifest_r = await client.get(_MANIFEST_URL, timeout=10)
            manifest_r.raise_for_status()
            manifest = manifest_r.json()
            entry = next((v for v in manifest["versions"] if v["id"] == version_str), None)
            if entry is None:
                raise RuntimeError(f"Unknown Minecraft version: {version_str}")
            meta_r = await client.get(entry["url"], timeout=10)
            meta_r.raise_for_status()
            server_dl = meta_r.json()["downloads"]["server"]
            jar_url: str = server_dl["url"]
            expected_sha1: str = server_dl["sha1"]
            dest = version_jar_path(version)
            dest.parent.mkdir(parents=True, exist_ok=True)
            h = hashlib.sha1()
            with dest.open("wb") as f:
                async with client.stream("GET", jar_url, timeout=300) as r:
                    r.raise_for_status()
                    async for chunk in r.aiter_bytes(65536):
                        f.write(chunk)
                        h.update(chunk)
            if h.hexdigest() != expected_sha1:
                dest.unlink()
                raise RuntimeError(f"SHA1 mismatch for {version_str}: expected {expected_sha1}, got {h.hexdigest()}")
    except httpx.TimeoutException:
        raise RuntimeError(f"Timed out while downloading Minecraft {version_str}") from None
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"HTTP {e.response.status_code} while downloading Minecraft {version_str}") from e
    except httpx.RequestError as e:
        raise RuntimeError(f"Network error while downloading Minecraft {version_str}: {e}") from e


async def ensure_version(version: int) -> None:
    if not version_jar_path(version).exists():
        await download_version(version)


async def fetch_available_versions(include_snapshots: bool = False) -> list[str]:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(_MANIFEST_URL, timeout=10)
            r.raise_for_status()
            data = r.json()
    except httpx.TimeoutException:
        raise RuntimeError("Timed out fetching Minecraft version list") from None
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"HTTP {e.response.status_code} fetching Minecraft version list") from e
    except httpx.RequestError as e:
        raise RuntimeError(f"Network error fetching Minecraft version list: {e}") from e
    return [v["id"] for v in data["versions"] if include_snapshots or v["type"] == "release"]
