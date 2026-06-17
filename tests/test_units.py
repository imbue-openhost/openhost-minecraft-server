import asyncio
import concurrent.futures
import hashlib
import importlib.util
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import httpx
import pytest

from server.java import required_java_version
from server.server import MinecraftServer
from server.server import ServerInfo
from server.state import AppState
from server.version_data import VERSION_MAP
from server.worlds import WorldInfo
from server.worlds import download_version
from server.worlds import ensure_version
from server.worlds import fetch_available_versions
from server.worlds import get_data_version
from server.worlds import get_version_string
from server.worlds import version_jar_path

# _VersionTableParser lives in the build script, not the runtime package.
_bvt_spec = importlib.util.spec_from_file_location(
    "build_version_table", Path(__file__).parent.parent / "scripts" / "build_version_table.py"
)
assert _bvt_spec is not None and _bvt_spec.loader is not None
_bvt = importlib.util.module_from_spec(_bvt_spec)
_bvt_spec.loader.exec_module(_bvt)
_VersionTableParser = _bvt._VersionTableParser


def _run(coro: Any) -> Any:
    """Run a coroutine in a fresh thread that has no existing event loop."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


class TestVersionTableParser:
    def test_extracts_version_mapping(self) -> None:
        html = """
        <table class="wikitable">
        <tr><th>Client version</th><th>Data version</th></tr>
        <tr><td><a>Java Edition 1.21.5</a></td><td>4325</td></tr>
        <tr><td><a>Java Edition 1.21.4</a></td><td>4189</td></tr>
        </table>
        """
        parser = _VersionTableParser()
        parser.feed(html)
        assert parser.result == {4325: "1.21.5", 4189: "1.21.4"}

    def test_strips_java_edition_prefix(self) -> None:
        html = '<table class="wikitable"><tr><td><a>Java Edition 26.2</a></td><td>4903</td></tr></table>'
        parser = _VersionTableParser()
        parser.feed(html)
        assert parser.result == {4903: "26.2"}

    def test_ignores_header_row(self) -> None:
        html = """
        <table class="wikitable">
        <tr><th>Client version</th><th>Data version</th></tr>
        <tr><td><a>Java Edition 1.21.5</a></td><td>4325</td></tr>
        </table>
        """
        parser = _VersionTableParser()
        parser.feed(html)
        assert len(parser.result) == 1

    def test_ignores_non_wikitable(self) -> None:
        html = '<table class="other"><tr><td><a>Java Edition 1.21.5</a></td><td>4325</td></tr></table>'
        parser = _VersionTableParser()
        parser.feed(html)
        assert parser.result == {}

    def test_skips_row_with_invalid_data_version(self) -> None:
        html = """
        <table class="wikitable">
        <tr><td><a>Java Edition 1.21.5</a></td><td>not-a-number</td></tr>
        <tr><td><a>Java Edition 1.21.4</a></td><td>4189</td></tr>
        </table>
        """
        parser = _VersionTableParser()
        parser.feed(html)
        assert parser.result == {4189: "1.21.4"}


class TestMinecraftServer:
    def _make(self, session_id: int = 0) -> MinecraftServer:
        return MinecraftServer(ServerInfo(version="1.21.5", world="test", memory_mb=2048), session_id)

    def test_initially_not_running(self) -> None:
        assert not self._make().is_running()

    def test_get_session_id(self) -> None:
        assert self._make(session_id=7).get_session_id() == 7

    def test_get_stats_returns_server_info(self) -> None:
        s = self._make()
        stats = s.get_stats()
        assert stats.world == "test"
        assert stats.version == "1.21.5"
        assert stats.memory_mb == 2048


class TestVersionLookup:
    def test_get_version_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(VERSION_MAP, 4325, "1.21.5")
        assert get_version_string(4325) == "1.21.5"

    def test_get_version_string_missing_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("server.worlds.VERSION_MAP", {})
        with pytest.raises(KeyError):
            get_version_string(9999)

    def test_get_data_version_roundtrip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(VERSION_MAP, 4325, "1.21.5")
        assert get_data_version("1.21.5") == 4325

    def test_get_data_version_unknown_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("server.worlds.VERSION_MAP", {})
        with pytest.raises(KeyError):
            get_data_version("nonexistent-version")


class TestAppStateWorldInsertion:
    def test_worlds_inserted_alphabetically(self) -> None:
        state = AppState()
        for name in ["gamma", "alpha", "beta"]:
            state.worlds.append(WorldInfo(version=100, world=name))
            state.worlds.sort(key=lambda w: w.world)
        assert [w.world for w in state.worlds] == ["alpha", "beta", "gamma"]

    def test_world_inserted_into_existing_sorted_list(self) -> None:
        state = AppState()
        state.worlds = [WorldInfo(version=100, world="apple"), WorldInfo(version=100, world="cherry")]
        new_world = WorldInfo(version=100, world="banana")
        state.worlds.append(new_world)
        state.worlds.sort(key=lambda w: w.world)
        assert [w.world for w in state.worlds] == ["apple", "banana", "cherry"]


# ── httpx mock helpers ────────────────────────────────────────────────────────


class _JsonResponse:
    def __init__(self, data: Any) -> None:
        self._data = data

    def raise_for_status(self) -> "_JsonResponse":
        return self

    def json(self) -> Any:
        return self._data


class _StreamCtx:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aenter__(self) -> "_StreamCtx":
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    def raise_for_status(self) -> None:
        pass

    async def aiter_bytes(self, chunk_size: int) -> AsyncGenerator[bytes, None]:
        for chunk in self._chunks:
            yield chunk


class _SequentialClient:
    """Minimal async httpx.AsyncClient stand-in: iterates responses for .get(), streams chunks."""

    def __init__(self, get_responses: list[Any], stream_chunks: list[bytes] | None = None) -> None:
        self._iter = iter(get_responses)
        self._stream_chunks = stream_chunks or []

    async def __aenter__(self) -> "_SequentialClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    async def get(self, url: str, *, timeout: float) -> Any:
        resp = next(self._iter)
        if isinstance(resp, BaseException):
            raise resp
        return resp

    def stream(self, method: str, url: str, *, timeout: float) -> _StreamCtx:
        return _StreamCtx(self._stream_chunks)


_MANIFEST = {"versions": [{"id": "1.21.5", "type": "release", "url": "http://meta.example/1.21.5.json"}]}
_JAR_BYTES = b"fake-jar-content"
_JAR_SHA1 = hashlib.sha1(_JAR_BYTES).hexdigest()
_META = {"downloads": {"server": {"url": "http://files.example/server.jar", "sha1": _JAR_SHA1}}}


# ── tests ─────────────────────────────────────────────────────────────────────


class TestFetchAvailableVersions:
    def test_returns_only_release_versions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        manifest = {
            "versions": [
                {"id": "1.21.5", "type": "release"},
                {"id": "1.21.5-rc1", "type": "snapshot"},
                {"id": "1.21.4", "type": "release"},
            ]
        }
        monkeypatch.setattr(httpx, "AsyncClient", lambda: _SequentialClient([_JsonResponse(manifest)]))
        assert _run(fetch_available_versions()) == ["1.21.5", "1.21.4"]

    def test_timeout_raises_runtime_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(httpx, "AsyncClient", lambda: _SequentialClient([httpx.TimeoutException("")]))
        with pytest.raises(RuntimeError, match="Timed out"):
            _run(fetch_available_versions())

    def test_http_error_raises_runtime_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        exc = httpx.HTTPStatusError("", request=httpx.Request("GET", "http://x"), response=httpx.Response(503))
        monkeypatch.setattr(httpx, "AsyncClient", lambda: _SequentialClient([exc]))
        with pytest.raises(RuntimeError, match="HTTP 503"):
            _run(fetch_available_versions())

    def test_request_error_raises_runtime_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(httpx, "AsyncClient", lambda: _SequentialClient([httpx.ConnectError("")]))
        with pytest.raises(RuntimeError, match="Network error"):
            _run(fetch_available_versions())


class TestDownloadVersion:
    def test_success_writes_jar(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("OPENHOST_APP_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("OPENHOST_APP_TEMP_DIR", str(tmp_path))
        client = _SequentialClient([_JsonResponse(_MANIFEST), _JsonResponse(_META)], stream_chunks=[_JAR_BYTES])
        monkeypatch.setattr(httpx, "AsyncClient", lambda: client)
        _run(download_version("1.21.5"))
        assert version_jar_path("1.21.5").read_bytes() == _JAR_BYTES

    def test_unknown_version_raises_runtime_error(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("OPENHOST_APP_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("OPENHOST_APP_TEMP_DIR", str(tmp_path))
        manifest = {"versions": [{"id": "1.20.0", "type": "release", "url": "http://x"}]}
        monkeypatch.setattr(httpx, "AsyncClient", lambda: _SequentialClient([_JsonResponse(manifest)]))
        with pytest.raises(RuntimeError, match="Unknown Minecraft version: 1.21.5"):
            _run(download_version("1.21.5"))

    def test_sha1_mismatch_raises_and_cleans_up(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("OPENHOST_APP_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("OPENHOST_APP_TEMP_DIR", str(tmp_path))
        bad_meta = {"downloads": {"server": {"url": "http://files.example/server.jar", "sha1": "deadbeef" * 5}}}
        client = _SequentialClient([_JsonResponse(_MANIFEST), _JsonResponse(bad_meta)], stream_chunks=[_JAR_BYTES])
        monkeypatch.setattr(httpx, "AsyncClient", lambda: client)
        with pytest.raises(RuntimeError, match="SHA1 mismatch"):
            _run(download_version("1.21.5"))
        assert not version_jar_path("1.21.5").exists()

    def test_timeout_raises_runtime_error(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("OPENHOST_APP_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("OPENHOST_APP_TEMP_DIR", str(tmp_path))
        monkeypatch.setattr(httpx, "AsyncClient", lambda: _SequentialClient([httpx.TimeoutException("")]))
        with pytest.raises(RuntimeError, match="Timed out"):
            _run(download_version("1.21.5"))

    def test_http_error_raises_runtime_error(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("OPENHOST_APP_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("OPENHOST_APP_TEMP_DIR", str(tmp_path))
        exc = httpx.HTTPStatusError("", request=httpx.Request("GET", "http://x"), response=httpx.Response(404))
        monkeypatch.setattr(httpx, "AsyncClient", lambda: _SequentialClient([exc]))
        with pytest.raises(RuntimeError, match="HTTP 404"):
            _run(download_version("1.21.5"))

    def test_request_error_raises_runtime_error(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("OPENHOST_APP_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("OPENHOST_APP_TEMP_DIR", str(tmp_path))
        monkeypatch.setattr(httpx, "AsyncClient", lambda: _SequentialClient([httpx.ConnectError("")]))
        with pytest.raises(RuntimeError, match="Network error"):
            _run(download_version("1.21.5"))


class TestEnsureVersion:
    def test_skips_download_if_jar_exists(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("OPENHOST_APP_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("OPENHOST_APP_TEMP_DIR", str(tmp_path))
        jar = version_jar_path("1.21.5")
        jar.parent.mkdir(parents=True, exist_ok=True)
        jar.write_bytes(b"existing")
        called: list[str] = []

        async def _fake_download(v: str) -> None:
            called.append(v)

        monkeypatch.setattr("server.worlds.download_version", _fake_download)
        _run(ensure_version("1.21.5"))
        assert called == []

    def test_downloads_if_jar_missing(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("OPENHOST_APP_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("OPENHOST_APP_TEMP_DIR", str(tmp_path))
        called: list[str] = []

        async def _fake_download(v: str) -> None:
            called.append(v)

        monkeypatch.setattr("server.worlds.download_version", _fake_download)
        _run(ensure_version("1.21.5"))
        assert called == ["1.21.5"]


class TestRequiredJavaVersion:
    def test_java_25_at_threshold(self) -> None:
        assert required_java_version(4764) == 25

    def test_java_21_between_thresholds(self) -> None:
        assert required_java_version(4671) == 21  # 1.21.11 — between 3827 and 4764

    def test_java_21_at_threshold(self) -> None:
        assert required_java_version(3827) == 21

    def test_java_17_just_below_java_21_threshold(self) -> None:
        assert required_java_version(3826) == 17

    def test_java_17_at_threshold(self) -> None:
        assert required_java_version(2848) == 17

    def test_java_16_just_below_java_17_threshold(self) -> None:
        assert required_java_version(2847) == 16

    def test_java_16_at_threshold(self) -> None:
        assert required_java_version(2714) == 16

    def test_java_8_just_below_java_16_threshold(self) -> None:
        assert required_java_version(2713) == 8

    def test_java_8_at_threshold(self) -> None:
        assert required_java_version(1122) == 8

    def test_raises_for_version_below_1_12(self) -> None:
        with pytest.raises(ValueError, match="predates supported range"):
            required_java_version(1121)
