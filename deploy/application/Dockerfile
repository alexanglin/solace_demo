# The Python 3.14 application image (docs/adr/0044): uv 0.12.5 by hash (docs/adr/0020), the
# locked workspace synchronized without development dependencies, and a numeric unprivileged
# user. The build context is the repository root; .dockerignore keeps secrets, caches, and
# the Agent Mesh project out of it. No service has an entrypoint yet, so the compose file
# gives each one an import probe as its command.
FROM python:3.14.7-slim-trixie@sha256:83ff1d245a3d57d04152252d3ef9cb361494d0b3395abd65a5ebe91c401c8e83 AS builder
COPY deploy/application/uv-requirements.txt /tmp/uv-requirements.txt
RUN pip install --no-cache-dir --require-hashes --requirement /tmp/uv-requirements.txt
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/app/.venv
ENV UV_PYTHON=/usr/local/bin/python3.14
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY services ./services
COPY tools ./tools
RUN uv sync --frozen --all-packages --no-dev

FROM python:3.14.7-slim-trixie@sha256:83ff1d245a3d57d04152252d3ef9cb361494d0b3395abd65a5ebe91c401c8e83
RUN groupadd --system --gid 10001 aerial && useradd --system --uid 10001 --gid 10001 --create-home aerial
COPY --from=builder --chown=10001:10001 /app /app
WORKDIR /app
USER 10001:10001
CMD ["/app/.venv/bin/python", "-c", "import aerial_rescue_contracts"]
