from collections.abc import Iterator

import pytest
from openhost_test_harness import OpenhostStack


@pytest.fixture(scope="session")
def stack() -> Iterator[OpenhostStack]:
    """Build the app's Dockerfile, run it under podman per openhost.toml, and
    front it with a mock OpenHost router that injects owner auth.

    OpenhostStack() finds openhost.toml by walking up from the cwd, so no app_dir
    is needed as long as tests run from within the app tree.

    - stack.url     — through the mock router (auth header injected, like a real owner request)
    - stack.app_url — direct to the container (control your own headers; eg the health probe)
    """
    with OpenhostStack() as s:
        yield s
