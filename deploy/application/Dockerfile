# Build the production dashboard with ADR-0103's exact Node and pnpm toolchain. The final
# application image receives only the production Vite output, never Node, pnpm, source tests,
# generated test evidence, or the dependency tree (ADR-0184).
FROM node:26.7.0-slim@sha256:5758d367d7b4f48b73a9bb3530e687e47efb289f3b43f9c0450a25225ae0db5d AS dashboard-builder
RUN npm install --global --ignore-scripts pnpm@11.23.0
WORKDIR /workspace/apps/dashboard
COPY apps/dashboard/package.json apps/dashboard/pnpm-lock.yaml apps/dashboard/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile --ignore-scripts
WORKDIR /workspace
COPY ./schemas ./schemas
COPY apps/dashboard/index.html apps/dashboard/tsconfig.json apps/dashboard/vite.config.ts ./apps/dashboard/
COPY apps/dashboard/src ./apps/dashboard/src
WORKDIR /workspace/apps/dashboard
RUN pnpm build \
    && ! grep --recursive --fixed-strings "__AERIAL_RESCUE_DASHBOARD_TEST__" dist \
    && ! grep --recursive --fixed-strings "TestFixtureSource" dist \
    && ! grep --recursive --fixed-strings "memory-only-test-bearer" dist \
    && ! grep --recursive --fixed-strings "synthetic-browser-bearer-do-not-persist" dist

# The Python 3.14 application image (docs/adr/0044): uv 0.12.5 by hash (docs/adr/0020), the
# locked workspace synchronized without development dependencies, and a numeric unprivileged
# user. The build context is the repository root; .dockerignore keeps secrets, caches, and
# the Agent Mesh project out of it. Compose selects one concrete long-running console per
# service; the image also carries the closed schemas and production scenario catalog those
# processes validate at startup.
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
COPY schemas ./schemas
COPY scenarios ./scenarios
RUN uv sync --frozen --all-packages --no-dev

FROM python:3.14.7-slim-trixie@sha256:83ff1d245a3d57d04152252d3ef9cb361494d0b3395abd65a5ebe91c401c8e83
RUN groupadd --system --gid 10001 aerial && useradd --system --uid 10001 --gid 10001 --create-home aerial
COPY --from=builder --chown=10001:10001 /app /app
COPY --from=dashboard-builder /workspace/apps/dashboard/dist /app/dashboard
RUN install --directory --owner=root --group=root --mode=0555 /app/replays
WORKDIR /app
USER 10001:10001
CMD ["/app/.venv/bin/python", "-c", "import aerial_rescue_contracts"]
