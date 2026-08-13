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
- Chat with the user can stay in their language; only durable artifacts are English-first.

## Development

- Install Python dependencies with `uv sync` and Web dependencies with `pnpm --dir web install`. `scripts/dev.sh` checks these prerequisites but does not install them.
- Run the local development environment with `scripts/dev.sh`. Vite serves the Web app and proxies `/api` to the loopback-only API backend; use `CUEWEAVER_MEDIA_ROOT`, `CUEWEAVER_WORK_ROOT`, `API_PORT`, and `WEB_PORT` to override defaults.
- Run backend tests with `scripts/test-backend.sh`, frontend tests with `scripts/test-frontend.sh`, frontend build checks with `scripts/lint-frontend.sh`, and Docker E2E with `scripts/test-e2e.sh`.
- Frontend scripts invoke the `pnpm` executable from `PATH`. CI provisions it with `pnpm/action-setup`; the Docker Web builder provisions it with Corepack.

## Runtime boundaries

- Docker is the complete production product boundary: it builds `web/dist`, copies it into `cueweaver/static`, and serves it from the production ASGI app. The Python package does not include generated Web assets.
- The production container operates on user-mounted Media and Work directories and must run as the matching host UID/GID, not root. Docker examples and E2E runs should pass `--user "$(id -u):$(id -g)"`; mounted Media must be readable and Work must be writable by that user.
- Use `uvicorn cueweaver.product:create_product_app_from_env --factory` for the production server. The development factory is API-only and is intended to run behind Vite.
- Production SPA fallback applies only to non-API routes. Unknown `/api` paths, including `/api` itself, must remain structured API 404 responses rather than returning `index.html`.
