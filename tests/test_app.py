import httpx
from openhost_test_harness import OpenhostStack
from playwright.sync_api import Page
from playwright.sync_api import expect


def test_health_endpoint(stack: OpenhostStack) -> None:
    response = httpx.get(f"{stack.app_url}/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_home_page_renders(stack: OpenhostStack, page: Page) -> None:
    page.goto(stack.url)
    expect(page.get_by_role("heading", name="app-template")).to_be_visible()
