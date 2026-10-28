from __future__ import annotations

from typing import Any

from fastapi import HTTPException


class ApiError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str, details: Any = None):
        super().__init__(status_code=status_code, detail={"code": code, "message": message, "details": details})


def not_found(resource: str) -> ApiError:
    return ApiError(404, "not_found", f"{resource} was not found.")

