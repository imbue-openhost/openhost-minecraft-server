default: test

# Install dependencies, pre-commit hooks, and the playwright chromium browser.
setup:
    uv sync
    uv run pre-commit install
    uv run playwright install chromium

# Run the app locally on http://localhost:8080 (auto-reloads on change).
run:
    OPENHOST_APP_DATA_DIR=app_data OPENHOST_APP_TEMP_DIR=app_temp_data OPENHOST_SQLITE_WORLDS=db/worlds.db uv run hypercorn server.app:app --bind 0.0.0.0:8080 --reload

# Build the container image and run it on http://localhost:8080 (persistent data in app_data/).
# Uses a separate temp dir from `just run` to avoid mixing macOS and Linux JRE binaries.
serve:
    podman build -t openhost-minecraft-servers .
    mkdir -p app_data_linux app_temp_data_linux
    podman run --rm -it \
        -p 8080:8080 \
        -p 25565:25565 -p 25566:25566 -p 25567:25567 -p 25568:25568 -p 25569:25569 \
        -e OPENHOST_APP_DATA_DIR=/app_data \
        -e OPENHOST_APP_TEMP_DIR=/app_temp \
        -e OPENHOST_SQLITE_WORLDS=/app_data/worlds.db \
        -v "$(pwd)/app_data_linux:/app_data:Z" \
        -v "$(pwd)/app_temp_data_linux:/app_temp:Z" \
        openhost-minecraft-servers

# Run the test suite.
test:
    uv run pytest -x

# Lint, format, and typecheck (same checks as the pre-commit hooks).
check:
    uv run ruff check --fix .
    uv run ruff format .
    uv run mypy

# Regenerate src/server/version_data.py from the Minecraft wiki.
gen-version-table:
    uv run python scripts/build_version_table.py

# Build the container image.
build:
    docker build -t openhost-minecraft-servers .

# Clean up local data and cached JREs.
clean:
    rm -rf app_data app_data_linux app_temp_data app_temp_data_linux db