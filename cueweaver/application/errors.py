"""Errors returned by CueWeaver application operations."""


class ServiceError(Exception):
    """A processing error safe to expose through the HTTP error envelope."""

    def __init__(self, error_code: str, message: str, **context: object) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.context = context
