"""Errors returned by CueWeaver application operations."""


class ServiceError(Exception):
    """A processing error safe to expose through the HTTP error envelope."""

    def __init__(self, error_code: str, message: str, **context: object) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.context = context


def project_service_error(error: ServiceError) -> dict[str, object]:
    """Project a ServiceError into the shared public error body."""
    return {
        "error_code": error.error_code,
        "message": error.message,
        **{
            key: str(value) if hasattr(value, "__fspath__") else value
            for key, value in error.context.items()
        },
    }
