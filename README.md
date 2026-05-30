# app-template


## using this as a template (remove me!)

1. `name` in `pyproject.toml` and `openhost.toml`, and the description in both.
2. Rename the package if you want something other than `server` (update
   `[tool.hatch.build.targets.wheel]`, `[tool.mypy]`, `[tool.ruff.lint.isort]`,
   the `Dockerfile` CMD, and `tests/conftest.py`).
3. Fill in this README and `openhost.toml` (resources, data, public paths).
4. Build your app in `src/server/`.

## development

```bash
just setup   # install deps, pre-commit hooks, and the playwright chromium browser
just run     # run locally on http://localhost:8080
just test    # run the test suite
just check   # lint, format, typecheck
```

Python work uses [uv](https://docs.astral.sh/uv/). Use `uv add <pkg>` to add a
dependency and `uv add --group dev <pkg>` for a dev-only one.

`just test` uses `openhost-test-harness`, which builds the Dockerfile and runs
the app under **podman** (so podman must be running on the host) fronted by a
mock OpenHost router. See `tests/` for the `stack` fixture.
