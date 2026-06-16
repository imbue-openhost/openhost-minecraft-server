import asyncio
import concurrent.futures
from typing import Any

import pytest


def _run(coro: Any) -> Any:
    """Run a coroutine in a fresh thread that has no existing event loop."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()

from server.server import MinecraftServer
from server.server import ServerInfo
from server.state import AppState
from server.worlds import VERSION_MAP
from server.worlds import WorldInfo
from server.worlds import _VersionTableParser
from server.worlds import get_data_version
from server.worlds import get_version_string


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
    def _make(self, id_num: int = 0) -> MinecraftServer:
        return MinecraftServer(ServerInfo(version="1.21.5", world="test", memory_mb=2048), id_num)

    def test_initially_not_running(self) -> None:
        assert not self._make().is_running()

    def test_run_sets_running(self) -> None:
        s = self._make()
        _run(s.run())
        assert s.is_running()

    def test_stop_clears_running(self) -> None:
        s = self._make()
        _run(s.run())
        _run(s.stop())
        assert not s.is_running()

    def test_get_id(self) -> None:
        assert self._make(id_num=7).get_id() == 7

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
