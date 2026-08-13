FROM node:24-bookworm-slim AS web-builder

WORKDIR /build/web
RUN corepack enable
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS runtime

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY cueweaver/ cueweaver/
RUN uv sync --frozen --no-dev
COPY --from=web-builder /build/web/dist/ cueweaver/static/

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000

CMD ["uvicorn", "cueweaver.product:create_product_app_from_env", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
