import asyncio
import os
import shutil
import stat
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx


async def fetch_loader_versions(loader: str, mc_version: str) -> list[str]:
    if loader == "fabric":
        return await _fetch_fabric_versions(mc_version)
    if loader == "forge":
        return await _fetch_forge_versions(mc_version)
    if loader == "neoforge":
        return await _fetch_neoforge_versions(mc_version)
    raise ValueError(f"Unknown loader: {loader!r}")


async def _fetch_fabric_versions(mc_version: str) -> list[str]:
    url = f"https://meta.fabricmc.net/v2/versions/loader/{mc_version}"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=15)
            r.raise_for_status()
        return [entry["loader"]["version"] for entry in r.json() if entry["loader"].get("stable")]
    except httpx.TimeoutException:
        raise RuntimeError("Timed out fetching Fabric version list") from None
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return []
        raise RuntimeError(f"HTTP {e.response.status_code} fetching Fabric version list") from e
    except (httpx.RequestError, KeyError, ValueError) as e:
        raise RuntimeError(f"Error fetching Fabric version list: {e}") from e


async def _fetch_forge_versions(mc_version: str) -> list[str]:
    url = "https://maven.minecraftforge.net/net/minecraftforge/forge/maven-metadata.xml"
    prefix = f"{mc_version}-"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=15)
            r.raise_for_status()
        root = ET.fromstring(r.text)
        versions = [
            v.text[len(prefix) :]
            for v in root.findall("./versioning/versions/version")
            if v.text and v.text.startswith(prefix)
        ]
        versions.reverse()
        return versions
    except httpx.TimeoutException:
        raise RuntimeError("Timed out fetching Forge version list") from None
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"HTTP {e.response.status_code} fetching Forge version list") from e
    except (httpx.RequestError, ET.ParseError) as e:
        raise RuntimeError(f"Error fetching Forge version list: {e}") from e


def _neoforge_version_prefix(mc_version: str) -> str:
    parts = mc_version.split(".")
    if len(parts) < 2:
        raise ValueError(f"Invalid MC version: {mc_version!r}")
    minor, patch = parts[1], parts[2] if len(parts) > 2 else "0"
    if minor == "20" and patch == "1":
        return "47."  # NeoForge 47.x was for MC 1.20.1
    return f"{minor}.{patch}."


async def _fetch_neoforge_versions(mc_version: str) -> list[str]:
    url = "https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml"
    try:
        prefix = _neoforge_version_prefix(mc_version)
    except ValueError:
        return []
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=15)
            r.raise_for_status()
        root = ET.fromstring(r.text)
        versions = [
            v.text
            for v in root.findall("./versioning/versions/version")
            if v.text and v.text.startswith(prefix) and "beta" not in v.text
        ]
        versions.reverse()
        return versions
    except httpx.TimeoutException:
        raise RuntimeError("Timed out fetching NeoForge version list") from None
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"HTTP {e.response.status_code} fetching NeoForge version list") from e
    except (httpx.RequestError, ET.ParseError) as e:
        raise RuntimeError(f"Error fetching NeoForge version list: {e}") from e


async def ensure_loader(
    world_dir: Path, mc_version: str, mod_loader: str, loader_version: str, java_bin: Path
) -> None:
    """Install the mod loader into world_dir if not already present. No-op for vanilla."""
    if mod_loader == "vanilla":
        return
    if mod_loader == "fabric":
        await _ensure_fabric(world_dir, mc_version, loader_version)
    elif mod_loader == "forge":
        await _ensure_forge(world_dir, mc_version, loader_version, java_bin)
    elif mod_loader == "neoforge":
        await _ensure_neoforge(world_dir, loader_version, java_bin)
    else:
        raise ValueError(f"Unknown mod loader: {mod_loader!r}")


async def _get_fabric_installer_version() -> str:
    url = "https://meta.fabricmc.net/v2/versions/installer"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, timeout=15)
        r.raise_for_status()
    for entry in r.json():
        if entry.get("stable"):
            return str(entry["version"])
    raise RuntimeError("No stable Fabric installer version found")


def _fabric_jar_cache_path(mc_version: str, loader_version: str) -> Path:
    return Path(os.environ["OPENHOST_APP_TEMP_DIR"]) / "fabric" / f"{mc_version}-{loader_version}.jar"


async def _ensure_fabric(world_dir: Path, mc_version: str, loader_version: str) -> None:
    cached = _fabric_jar_cache_path(mc_version, loader_version)
    if cached.exists():
        return
    installer_version = await _get_fabric_installer_version()
    url = f"https://meta.fabricmc.net/v2/versions/loader/{mc_version}/{loader_version}/{installer_version}/server/jar"
    cached.parent.mkdir(parents=True, exist_ok=True)
    await _download_to(url, cached, f"Fabric {loader_version} server launcher")


async def _run_installer(world_dir: Path, java_bin: Path, installer_path: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        str(java_bin),
        "-jar",
        str(installer_path),
        "--installServer",
        cwd=world_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"Mod loader installer failed (exit {proc.returncode}):\n" + stdout.decode(errors="replace")[-3000:]
        )


async def _ensure_forge(world_dir: Path, mc_version: str, forge_version: str, java_bin: Path) -> None:
    if (world_dir / "run.sh").exists() or list(world_dir.glob("forge-*-universal.jar")):
        return
    installer_name = f"forge-{mc_version}-{forge_version}-installer.jar"
    url = f"https://maven.minecraftforge.net/net/minecraftforge/forge/{mc_version}-{forge_version}/{installer_name}"
    installer_path = world_dir / installer_name
    await _download_to(url, installer_path, f"Forge {forge_version} installer")
    try:
        await _run_installer(world_dir, java_bin, installer_path)
    finally:
        installer_path.unlink(missing_ok=True)
    _make_executable(world_dir / "run.sh")


async def _ensure_neoforge(world_dir: Path, neoforge_version: str, java_bin: Path) -> None:
    if (world_dir / "run.sh").exists():
        return
    installer_name = f"neoforge-{neoforge_version}-installer.jar"
    url = f"https://maven.neoforged.net/releases/net/neoforged/neoforge/{neoforge_version}/{installer_name}"
    installer_path = world_dir / installer_name
    await _download_to(url, installer_path, f"NeoForge {neoforge_version} installer")
    try:
        await _run_installer(world_dir, java_bin, installer_path)
    finally:
        installer_path.unlink(missing_ok=True)
    _make_executable(world_dir / "run.sh")


async def _download_to(url: str, dest: Path, label: str) -> None:
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            async with client.stream("GET", url, timeout=300) as r:
                r.raise_for_status()
                with dest.open("wb") as f:
                    async for chunk in r.aiter_bytes(65536):
                        f.write(chunk)
    except httpx.TimeoutException:
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"Timed out downloading {label}") from None
    except httpx.HTTPStatusError as e:
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"HTTP {e.response.status_code} downloading {label}") from e
    except httpx.RequestError as e:
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"Network error downloading {label}: {e}") from e


def _make_executable(path: Path) -> None:
    if path.exists():
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def get_launch_cmd(
    world_dir: Path, mc_version: str, mod_loader: str, loader_version: str, memory_mb: int, java_bin: Path
) -> tuple[list[str], dict[str, str]]:
    """Return (argv, extra_env) for launching a modded server process."""
    if mod_loader == "fabric":
        jar = _fabric_jar_cache_path(mc_version, loader_version)
        return (
            [str(java_bin), f"-Xmx{memory_mb}M", f"-Xms{memory_mb}M", "-jar", str(jar), "--nogui"],
            {},
        )
    if mod_loader in ("forge", "neoforge"):
        run_sh = world_dir / "run.sh"
        if run_sh.exists():
            _write_jvm_args(world_dir / "user_jvm_args.txt", memory_mb)
            extra_env = {"PATH": str(java_bin.parent) + ":" + os.environ.get("PATH", "")}
            return (["/bin/sh", str(run_sh), "--nogui"], extra_env)
        candidates = list(world_dir.glob("forge-*-universal.jar")) + list(world_dir.glob("forge-*-server.jar"))
        if not candidates:
            raise RuntimeError(f"No {mod_loader} server found in {world_dir} — installation may have failed")
        return (
            [str(java_bin), f"-Xmx{memory_mb}M", f"-Xms{memory_mb}M", "-jar", str(candidates[0]), "--nogui"],
            {},
        )
    raise ValueError(f"Unknown mod loader: {mod_loader!r}")


def _write_jvm_args(path: Path, memory_mb: int) -> None:
    path.write_text(f"-Xmx{memory_mb}M\n-Xms{memory_mb}M\n")


def cleanup_loader_files(world_dir: Path, loader: str) -> None:
    """Remove installed files for a given loader so it can be reinstalled."""
    if loader in ("forge", "neoforge"):
        for name in ("run.sh", "run.bat", "user_jvm_args.txt"):
            (world_dir / name).unlink(missing_ok=True)
        libs = world_dir / "libraries"
        if libs.exists():
            shutil.rmtree(libs)
    # fabric: launcher JAR lives in the shared cache; nothing to clean in world_dir
