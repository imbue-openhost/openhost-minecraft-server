import asyncio
import os
import platform
import tarfile
import tempfile
from pathlib import Path

import httpx

from server.version_data import JAVA_VERSION_TABLE as _JAVA_VERSION_TABLE

_ADOPTIUM_BASE = "https://api.adoptium.net/v3/binary/latest"


def required_java_version(data_version: int) -> int:
    """Return the Java feature version (8, 17, or 21) required for the given Minecraft data version."""
    for threshold, java_ver in _JAVA_VERSION_TABLE:
        if data_version >= threshold:
            return java_ver
    raise ValueError(f"Minecraft data version {data_version} predates supported range (17w13a / 1.12)")


def _adoptium_os_arch() -> tuple[str, str]:
    sys = platform.system()
    os_name = "linux" if sys == "Linux" else "mac"
    machine = platform.machine().lower()
    arch = "aarch64" if machine in ("arm64", "aarch64") else "x64"
    return os_name, arch


def _temp_dir() -> Path:
    return Path(os.environ["OPENHOST_APP_TEMP_DIR"])


def _jre_base() -> Path:
    return _temp_dir() / "jre"


def _java_bin(java_version: int) -> Path:
    base = _jre_base()
    if not base.exists():
        raise FileNotFoundError(f"No JREs cached at {base}")
    for entry in sorted(base.iterdir()):
        if entry.is_dir() and entry.name.startswith(f"jdk-{java_version}."):
            for candidate in entry.rglob("bin/java"):
                if candidate.is_file():
                    return candidate.resolve()
    raise FileNotFoundError(f"No Java {java_version} JRE found under {base}")


def list_downloaded_java_versions() -> list[int]:
    base = _jre_base()
    if not base.exists():
        return []
    versions: set[int] = set()
    for entry in base.iterdir():
        if entry.is_dir() and entry.name.startswith("jdk-"):
            try:
                versions.add(int(entry.name[4:].split(".")[0]))
            except ValueError:
                pass
    return sorted(versions)


def is_java_downloaded(java_version: int) -> bool:
    try:
        _java_bin(java_version)
        return True
    except FileNotFoundError:
        return False


def _extract_tarball(archive: Path, dest: Path) -> None:
    with tarfile.open(archive) as tf:
        tf.extractall(dest, filter="data")


async def ensure_java(java_version: int) -> Path:
    """Return path to java binary for the given feature version, downloading the JRE if needed."""
    try:
        return _java_bin(java_version)
    except FileNotFoundError:
        pass

    os_name, arch = _adoptium_os_arch()
    url = f"{_ADOPTIUM_BASE}/{java_version}/ga/{os_name}/{arch}/jre/hotspot/normal/eclipse"

    dest_dir = _jre_base()
    dest_dir.mkdir(parents=True, exist_ok=True)

    tmp_fd, tmp_name = tempfile.mkstemp(suffix=".tar.gz")
    tmp_path = Path(tmp_name)
    os.close(tmp_fd)
    try:
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                async with client.stream("GET", url, timeout=300) as r:
                    r.raise_for_status()
                    with tmp_path.open("wb") as f:
                        async for chunk in r.aiter_bytes(65536):
                            f.write(chunk)
        except httpx.TimeoutException:
            raise RuntimeError(f"Timed out downloading Java {java_version} JRE") from None
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"HTTP {e.response.status_code} downloading Java {java_version} JRE") from e
        except httpx.RequestError as e:
            raise RuntimeError(f"Network error downloading Java {java_version} JRE: {e}") from e
        await asyncio.to_thread(_extract_tarball, tmp_path, dest_dir)
    finally:
        tmp_path.unlink(missing_ok=True)

    try:
        return _java_bin(java_version)
    except FileNotFoundError:
        raise RuntimeError(f"Java {java_version} JRE was extracted but bin/java could not be located") from None
