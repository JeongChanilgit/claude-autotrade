"""Error handling glue for the dashboard REST API.

Exposes ``KairosAPIError`` — the single typed exception routes raise — plus
``register_exception_handlers`` which wires it (and FastAPI's validation
error) into the frozen ``{"error": {"code": .., "message": ..}}`` shape.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


# Known error codes — kept in sync with dtos.ErrorCode. STRATEGY_NOT_FOUND is
# the Phase 5 addition, raised by /api/runs when the request references an
# unregistered strategy name.
KNOWN_ERROR_CODES: tuple[str, ...] = (
    "RUN_NOT_FOUND",
    "VALIDATION_ERROR",
    "DATA_UNAVAILABLE",
    "ENGINE_ERROR",
    "INTERNAL_ERROR",
    "STRATEGY_NOT_FOUND",
)


class KairosAPIError(Exception):
    """Typed REST error. ``code`` must be one of the dtos.ErrorCode literals."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _error_body(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": message}}


def register_exception_handlers(app: FastAPI) -> None:
    """Register the two exception handlers on ``app``."""

    @app.exception_handler(KairosAPIError)
    async def _kairos_error(_request: Request, exc: KairosAPIError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content=_error_body(exc.code, exc.message),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Surface pydantic's own message list so callers can debug malformed
        # bodies, but keep the envelope stable.
        errors = exc.errors()
        if errors:
            first = errors[0]
            loc = ".".join(str(p) for p in first.get("loc", ()))
            msg = first.get("msg", "validation error")
            message = f"{loc}: {msg}" if loc else msg
        else:
            message = "validation error"
        return JSONResponse(
            status_code=422,
            content=_error_body("VALIDATION_ERROR", message),
        )


__all__ = ["KNOWN_ERROR_CODES", "KairosAPIError", "register_exception_handlers"]
