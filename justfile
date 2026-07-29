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
