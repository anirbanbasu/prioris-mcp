# Install minimal project dependencies in a virtual environment
install:
    @echo "Installing project dependencies in a virtual environment..."
    @uv sync --no-dev
    @echo "Project dependencies installed."

# Install all project dependencies in a virtual environment
install-all:
    @echo "Installing all project dependencies in a virtual environment..."
    @uv sync --all-groups
    @echo "All project dependencies installed."

# Install pre-commit hooks using 'prek'
install-pre-commit-hooks:
    @echo "Installing pre-commit hooks using prek..."
    @prek install
    @echo "Pre-commit hooks installed."

# Update pre-commit hooks using 'prek'
pre-commit-update:
    @echo "Updating pre-commit hooks using prek..."
    @prek auto-update
    @echo "Pre-commit hooks updated."

# Upgrade project dependencies using 'uv'
upgrade-dependencies:
    @echo "Upgrading project dependencies..."
    @uv lock -U
    @echo "Dependencies upgraded."

# Bump the patch version of the project using 'uv'
bump-patch:
    @echo "Updating current project version: $(uv version --short)"
    @uv version --bump patch
    @echo "Updated project to: $(uv version --short)"

# Format the code
format:
    @echo "Formatting code..."
    @uv run ruff format
    @uv run ruff check --fix --fix-only
    @echo "Code formatted."

# Run the type checker
type-check:
    @echo "Running type checker..."
    @uv run ty check
    @echo "Type checking complete."

export PRIORIS_MCP_TRANSPORT := "streamable-http"
# Set CORS allowed origins for the ASGI application to the MCP Inspector's default host and port
export PRIORIS_MCP_ASGI_CORS_ALLOWED_ORIGINS := "http://localhost:6274"

# Run the prioris-mcp application with the specified transport
run-streamable-http:
    @echo "Running prioris-mcp with $PRIORIS_MCP_TRANSPORT transport..."
    @uv run prioris-mcp
    @echo "prioris-mcp shut down."

# Run tests with coverage reporting
test-coverage:
    @echo "Running tests with coverage..."
    @uv run --group test coverage run -m pytest --capture=tee-sys -vvv --log-cli-level=INFO tests/
    @uv run coverage report -m
    @echo "Test coverage complete."

# Launch MCP Inspector for debugging
launch-inspector:
    #!/usr/bin/env bash
    echo "Launching MCP Inspector..."
    if [ ! -f ~/.nvm/nvm.sh ]; then
        echo "Error: nvm is not installed or ~/.nvm/nvm.sh does not exist."
        echo "Please install nvm from https://github.com/nvm-sh/nvm or ensure npx is available in your PATH."
        exit 1
    fi
    . ~/.nvm/nvm.sh && nvm use --lts && npx @modelcontextprotocol/inspector

# Start the documentation server
start-documentation-server docs_host="0.0.0.0" docs_port="8888":
    @echo "Launching documentation server at http://{{ docs_host }}:{{ docs_port }}..."
    @uv run zensical serve -a {{ docs_host }}:{{ docs_port }}
    @echo "Documentation server shut down."

# Run the Open Source Vulnerability scanner
vulnerability-scan:
    @echo "Running Open Source Vulnerability scanner..."
    @osv-scanner scan source -r .
    @echo "Vulnerability scan complete."

# Render a PlantUML diagram to SVG or PNG using an ephemeral plantuml-server Docker container
# with the vendored Inter font installed. Output is named after the .puml file's own basename,
# so the file must declare a matching `@startuml <basename>`. dpi only affects PNG output; add
# `skinparam dpi <n>` in the .puml source itself to control it.
render-plantuml file format="svg" outdir="docs/images":
    #!/usr/bin/env bash
    set -euo pipefail
    container="plantuml-session"
    fontdir="/usr/share/fonts/truetype/inter"

    if ! docker ps --format '{{{{.Names}}' | grep -qx "$container"; then
        echo "Starting ephemeral plantuml-server container '$container'..."
        docker run -d --rm --name "$container" plantuml/plantuml-server:latest >/dev/null
    fi

    jar=""
    for i in $(seq 1 30); do
        jar=$(docker exec "$container" sh -c "find /tmp -maxdepth 6 -iname 'plantuml*.jar' 2>/dev/null" | head -1)
        [ -n "$jar" ] && break
        sleep 1
    done
    if [ -z "$jar" ]; then
        echo "Error: plantuml.jar did not appear inside '$container' in time." >&2
        exit 1
    fi

    if ! docker exec "$container" test -f "$fontdir/Inter-Regular.ttf" 2>/dev/null; then
        echo "Installing Inter font into container..."
        docker exec --user root "$container" mkdir -p "$fontdir"
        for f in diagrams/fonts/inter/*.ttf; do
            docker cp "$f" "$container:$fontdir/"
        done
        docker exec --user root "$container" fc-cache -f >/dev/null
    fi

    name=$(basename "{{file}}" .puml)
    docker cp "{{file}}" "$container:/tmp/${name}.puml"
    docker exec "$container" /opt/java/openjdk/bin/java -jar "$jar" -t{{format}} -charset UTF-8 "/tmp/${name}.puml"
    if [ "{{format}}" = "svg" ]; then
        # PlantUML emits a single quoted font-family (e.g. font-family="'Inter'") with no
        # fallback, so viewers without Inter installed substitute a font while the SVG still
        # forces glyphs to Inter-measured textLength widths, distorting the substitute badly.
        # Rewriting to a real CSS fallback list at least substitutes a metrics-similar sans-serif.
        docker exec "$container" sed -i "s/font-family=\"'Inter'\"/font-family=\"Inter, sans-serif\"/g" "/tmp/${name}.svg"
    fi
    mkdir -p "{{outdir}}"
    docker cp "$container:/tmp/${name}.{{format}}" "{{outdir}}/${name}.{{format}}"
    echo "Rendered {{outdir}}/${name}.{{format}}"

# Stop the ephemeral plantuml-server container started by render-plantuml
stop-plantuml-session:
    @docker stop plantuml-session >/dev/null 2>&1 && echo "Stopped plantuml-session." || echo "plantuml-session is not running."
