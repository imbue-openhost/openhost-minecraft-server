import hashlib
import os
from html.parser import HTMLParser
from pathlib import Path

import attr
import httpx

_MANIFEST_URL = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"
_DATA_VERSION_API = (
    "https://minecraft.wiki/api.php?action=parse&page=Data_version&prop=text&format=json&formatversion=2"
)

VERSION_MAP: dict[int, str] = {}


class _VersionTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_table = False
        self._in_row = False
        self._in_td = False
        self._td_index = 0
        self._version_text = ""
        self._dv_text = ""
        self.result: dict[int, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        if tag == "table" and "wikitable" in (attr_dict.get("class") or ""):
            self._in_table = True
        elif self._in_table and tag == "tr":
            self._in_row = True
            self._td_index = 0
            self._version_text = ""
            self._dv_text = ""
        elif self._in_table and self._in_row and tag == "td":
            self._in_td = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            self._in_table = False
        elif self._in_table and tag == "td":
            self._in_td = False
            self._td_index += 1
        elif self._in_table and self._in_row and tag == "tr":
            self._in_row = False
            version = self._version_text.strip().removeprefix("Java Edition ")
            try:
                dv = int(self._dv_text.strip())
                if version:
                    self.result[dv] = version
            except ValueError:
                pass

    def handle_data(self, data: str) -> None:
        if self._in_td:
            if self._td_index == 0:
                self._version_text += data
            elif self._td_index == 1:
                self._dv_text += data


@attr.s(auto_attribs=True, frozen=True)
class WorldInfo:
    version: int
    world: str


def _data_dir() -> Path:
    return Path(os.environ["OPENHOST_APP_DATA_DIR"])


def get_version_string(version: int) -> str:
    return VERSION_MAP[version]


def get_data_version(version_str: str) -> int:
    for dv, vs in VERSION_MAP.items():
        if vs == version_str:
            return dv
    raise KeyError(f"Unknown version: {version_str}")


def read_world_version(name: str) -> int:
    # Placeholder: real implementation reads DataVersion from level.dat NBT.
    return int((_data_dir() / "worlds" / name / "version.txt").read_text().strip())


def is_world(name: str) -> bool:
    return True


def get_world(name: str) -> WorldInfo | None:
    try:
        version = read_world_version(name)
    except (ValueError, FileNotFoundError, OSError):
        return None
    return WorldInfo(version=version, world=name)


def load_worlds() -> list[WorldInfo]:
    d = _data_dir() / "worlds"
    if not d.exists():
        return []
    result = []
    for p in sorted(d.iterdir()):
        if p.is_dir() and is_world(p.name):
            world = get_world(p.name)
            if world is not None:
                result.append(world)
    return result


def create_world(name: str, version_str: str) -> None:
    d = _data_dir() / "worlds" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "version.txt").write_text(str(get_data_version(version_str)))


def version_jar_path(version_str: str) -> Path:
    return _data_dir() / "versions" / f"{version_str}.jar"


def list_downloaded_versions() -> list[str]:
    d = _data_dir() / "versions"
    if not d.exists():
        return []
    return sorted(p.stem for p in d.iterdir() if p.suffix == ".jar")


async def download_version(version_str: str) -> None:
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
            dest = version_jar_path(version_str)
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
                raise RuntimeError(
                    f"SHA1 mismatch for {version_str}: expected {expected_sha1}, got {h.hexdigest()}"
                )
    except httpx.TimeoutException:
        raise RuntimeError(f"Timed out while downloading Minecraft {version_str}")
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"HTTP {e.response.status_code} while downloading Minecraft {version_str}")
    except httpx.RequestError as e:
        raise RuntimeError(f"Network error while downloading Minecraft {version_str}: {e}")


async def ensure_version(version_str: str) -> None:
    if not version_jar_path(version_str).exists():
        await download_version(version_str)


async def fetch_available_versions(include_snapshots: bool = False) -> list[str]:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(_MANIFEST_URL, timeout=10)
            r.raise_for_status()
            data = r.json()
    except httpx.TimeoutException:
        raise RuntimeError("Timed out fetching Minecraft version list")
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"HTTP {e.response.status_code} fetching Minecraft version list")
    except httpx.RequestError as e:
        raise RuntimeError(f"Network error fetching Minecraft version list: {e}")
    return [v["id"] for v in data["versions"] if include_snapshots or v["type"] == "release"]


async def fetch_data_versions() -> dict[int, str]:
    async with httpx.AsyncClient() as client:
        r = await client.get(_DATA_VERSION_API, timeout=15)
        r.raise_for_status()
        html: str = r.json()["parse"]["text"]
    parser = _VersionTableParser()
    parser.feed(html)
    return parser.result
