import contextlib
import hashlib
import json
import os
import shutil
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
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(worlds)")}
    for col, defn in [
        ("mod_loader", "TEXT NOT NULL DEFAULT 'vanilla'"),
        ("mod_loader_version", "TEXT NOT NULL DEFAULT ''"),
    ]:
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE worlds ADD COLUMN {col} {defn}")
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


def save_world_info(
    name: str, port: int, version: int, mod_loader: str = "vanilla", mod_loader_version: str = ""
) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT INTO worlds (name, port, version, mod_loader, mod_loader_version) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET port=excluded.port, version=excluded.version, "
            "mod_loader=excluded.mod_loader, mod_loader_version=excluded.mod_loader_version",
            (name, port, version, mod_loader, mod_loader_version),
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
        row = conn.execute(
            "SELECT port, version, mod_loader, mod_loader_version FROM worlds WHERE name = ?", (name,)
        ).fetchone()
    if row is None:
        return None
    port, version, mod_loader, mod_loader_version = int(row[0]), int(row[1]), row[2], row[3]
    if version == 0:
        try:
            version = get_version(name)
        except LookupError:
            return None
    return WorldInfo(
        version=version, name=name, port=port, mod_loader=mod_loader, mod_loader_version=mod_loader_version
    )


def get_world_loader_info(name: str) -> tuple[str, str]:
    with _db() as conn:
        row = conn.execute("SELECT mod_loader, mod_loader_version FROM worlds WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise KeyError(f"No loader info found for world {name!r}")
    return row[0], row[1]


def delete_world(name: str) -> None:
    with _db() as conn:
        conn.execute("DELETE FROM worlds WHERE name = ?", (name,))
    d = _data_dir() / "worlds" / name
    if d.exists():
        shutil.rmtree(d)


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
    if info.mod_loader != "vanilla":
        (d / "mods").mkdir(exist_ok=True)
    save_world_info(info.name, info.port, info.version, info.mod_loader, info.mod_loader_version)


def world_dir(name: str) -> Path:
    return _data_dir() / "worlds" / name


def _extract_world_zip(zip_path: Path, dest: Path) -> None:
    dest_str = str(dest.resolve()) + os.sep
    try:
        with zipfile.ZipFile(zip_path) as zf:
            members = zf.namelist()
            roots = {m.split("/")[0] for m in members if m.strip("/")}
            strip_prefix = (next(iter(roots)) + "/") if len(roots) == 1 else ""
            for member in members:
                rel = member[len(strip_prefix) :] if strip_prefix and member.startswith(strip_prefix) else member
                if not rel or rel.endswith("/"):
                    continue
                target = (dest / rel).resolve()
                if not str(target).startswith(dest_str):
                    raise ValueError(f"Zip contains unsafe path: {member!r}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, target.open("wb") as f:
                    shutil.copyfileobj(src, f)
    except zipfile.BadZipFile as e:
        raise ValueError(f"Invalid zip file: {e}") from e


def import_world_from_zip(
    zip_bytes: bytes, name: str, port: int, version: int, mod_loader: str = "vanilla", mod_loader_version: str = ""
) -> None:
    dest = _data_dir() / "worlds" / name
    if dest.exists():
        raise ValueError(f"World '{name}' already exists")
    temp_zip = _temp_dir() / f"import_{name}.zip"
    dest.mkdir(parents=True)
    try:
        temp_zip.write_bytes(zip_bytes)
        _extract_world_zip(temp_zip, dest)
        if not (dest / "level.dat").exists():
            raise ValueError("Zip does not appear to contain a Minecraft world (no level.dat found)")
        (dest / "eula.txt").write_text("eula=true\n")
        if mod_loader != "vanilla":
            (dest / "mods").mkdir(exist_ok=True)
        save_world_info(name, port, version, mod_loader, mod_loader_version)
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    finally:
        temp_zip.unlink(missing_ok=True)


_CONFIG_KEYS = [
    "motd",
    "max-players",
    "difficulty",
    "gamemode",
    "pvp",
    "spawn-protection",
    "view-distance",
    "simulation-distance",
    "allow-flight",
    "allow-nether",
]

_CONFIG_DEFAULTS: dict[str, str] = {
    "motd": "A Minecraft Server",
    "max-players": "20",
    "difficulty": "easy",
    "gamemode": "survival",
    "pvp": "true",
    "spawn-protection": "16",
    "view-distance": "10",
    "simulation-distance": "10",
    "allow-flight": "false",
    "allow-nether": "true",
}


def read_world_config(name: str) -> dict[str, str]:
    props = world_dir(name) / "server.properties"
    result = dict(_CONFIG_DEFAULTS)
    if not props.exists():
        return result
    for line in props.read_text().splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key in _CONFIG_KEYS:
            result[key] = val.strip()
    return result


def write_world_config(name: str, updates: dict[str, str]) -> None:
    props = world_dir(name) / "server.properties"
    if props.exists():
        lines = props.read_text().splitlines()
    else:
        lines = []
    updated: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        key = stripped.partition("=")[0].strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}")
            updated.add(key)
        else:
            new_lines.append(line)
    for key, val in updates.items():
        if key not in updated:
            new_lines.append(f"{key}={val}")
    props.write_text("\n".join(new_lines) + "\n")


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
