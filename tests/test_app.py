import httpx
from openhost_test_harness import OpenhostStack
from playwright.sync_api import Page
from playwright.sync_api import expect


def test_health_endpoint(stack: OpenhostStack) -> None:
    response = httpx.get(f"{stack.app_url}/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_home_page_renders(stack: OpenhostStack, page: Page) -> None:
    stack.playwright_login(page)
    page.goto(stack.url)
    expect(page.get_by_role("heading", name="Minecraft Server")).to_be_visible()


def test_api_worlds_returns_list(stack: OpenhostStack) -> None:
    response = stack.owner_session.get(f"{stack.url}/api/worlds")
    assert response.status_code == 200
    worlds = response.json()
    assert isinstance(worlds, list)
    assert all("name" in w and "version" in w for w in worlds)


def test_api_servers_empty_on_startup(stack: OpenhostStack) -> None:
    response = stack.owner_session.get(f"{stack.url}/api/servers")
    assert response.status_code == 200
    assert response.json() == []


def test_api_versions_returns_list(stack: OpenhostStack) -> None:
    response = stack.owner_session.get(f"{stack.url}/api/versions")
    assert response.status_code == 200
    versions = response.json()
    assert isinstance(versions, list)
    assert len(versions) > 0
    assert all(isinstance(v, str) for v in versions)


def test_start_nonexistent_world_fails(stack: OpenhostStack) -> None:
    response = stack.owner_session.post(
        f"{stack.url}/api/server/start",
        json={"world": "no-such-world", "memory_mb": 2048},
    )
    assert response.status_code >= 400


def test_stop_nonexistent_server_fails(stack: OpenhostStack) -> None:
    response = stack.owner_session.post(f"{stack.url}/api/server/stop?session_id=9999")
    assert response.status_code >= 400
