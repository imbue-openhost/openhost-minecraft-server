import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path

import httpx

from server.datatypes import WorldInfo
from server.version_data import VERSION_MAP

_MANIFEST_URL = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"


def _data_dir() -> Path:
    return Path(os.environ["OPENHOST_APP_DATA_DIR"])


def _temp_dir() -> Path:
    return Path(os.environ["OPENHOST_APP_TEMP_DIR"])


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
    d = _data_dir() / "worlds" / name
    jars = list(d.glob("*.jar"))
    if not jars:
        raise LookupError(f"World with name {name} does not exist")
    return read_jar_data_version(jars[0])


def get_world(name: str) -> WorldInfo | None:
    return WorldInfo(version=get_version(name), name=name)


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
    shutil.copy2(version_jar_path(info.version), d / f"{get_version_string(info.version)}.jar")
    (d / "eula.txt").write_text("eula=true\n")


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
