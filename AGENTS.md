# CueWeaver

## Agent skills

### Issue tracker

Issues and specs for this repo live as GitHub issues, managed via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical roles map to the default label strings: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Writing style

- Write repository documentation (docs, issues, tickets, README, CONTEXT.md, ADRs) in English.

## Development

- Install Python dependencies with `uv sync` and Web dependencies with `pnpm --dir web install`. `scripts/dev.sh` checks these prerequisites but does not install them.
- Run commands from the repository root.
- Run the local development environment with `scripts/dev.sh`. Vite serves the Web app and proxies `/api` to the loopback-only API backend; use `CUEWEAVER_MEDIA_ROOT`, `CUEWEAVER_WORK_ROOT`, `API_PORT`, and `WEB_PORT` to override defaults.
- Run backend tests with `scripts/test-backend.sh`, frontend tests with `scripts/test-frontend.sh`, and Docker E2E with `scripts/test-e2e.sh`.
- Run static checks and the frontend build with `scripts/lint-backend.sh --check` and `scripts/lint-frontend.sh --check`; the `--check` flag prevents formatting changes.
- Run focused tests with `uv run pytest -q tests/test_product_app.py` or `pnpm --dir web exec vitest run tests/app.test.tsx`.
- Build the production image with `docker build -t cueweaver .`; production runtime and mount requirements are documented in `README.md`.
- Frontend scripts invoke the `pnpm` executable from `PATH`. CI provisions it with `pnpm/action-setup`; the Docker Web builder provisions it with Corepack.

## Gotchas

- `scripts/dev.sh` creates default development roots under `.cueweaver/dev/`; custom `CUEWEAVER_MEDIA_ROOT` and `CUEWEAVER_WORK_ROOT` values must be absolute, and a custom Media root must already exist.
- Docker is the complete production product boundary: it builds `web/dist`, copies it into `cueweaver/static`, and serves it from the production ASGI app. The Python package does not include generated Web assets.
- Production mounts need readable Media, a writable selected Media directory for publishing output, and a writable Work root. E2E runs use `--user "$(id -u):$(id -g)"` so bind-mounted test files remain owned by the test runner.
- Use `uvicorn cueweaver.product:create_product_app_from_env --factory` for production; the development factory is API-only and runs behind Vite.
- Production SPA fallback applies only to non-API routes. Unknown `/api` paths, including `/api` itself, must remain structured API 404 responses rather than returning `index.html`.
- An unconfigured PySubtrans provider does not prevent startup; configure it in PySubtrans service settings and restart before creating translation Jobs.
