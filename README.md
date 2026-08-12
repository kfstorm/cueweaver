# CueWeaver

CueWeaver is a library for a local HTTP subtitle service. It exposes application
components for exactly three synchronous JSON operations:

- `POST /api/discover`
- `POST /api/extract`
- `POST /api/translate`

Requests use explicit container-local paths. Discovery reports External and
Embedded subtitle candidates, extraction writes a selected text stream to an
explicit path, and translation reads and writes explicit subtitle paths.

The project does not provide a CLI or an HTTP server startup entrypoint. An
embedding service creates its own ASGI application with
`cueweaver.create_app(cueweaver.CueWeaverApplication())`.

Translation provider configuration remains PySubtrans service-process
configuration. CueWeaver does not add provider configuration request fields or
CueWeaver-specific environment-variable fallbacks.

## Test

```bash
uv run pytest
```

## Development Checks

```bash
scripts/lint.sh --check
```
