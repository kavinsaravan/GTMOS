"""Shared response envelope and error type.

Every response uses {status, data, warnings, source_ids, operation_id}.
"""
from __future__ import annotations

import uuid
from typing import Any


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def ok(data: Any, operation_id: str, warnings: list[str] | None = None,
       source_ids: list[str] | None = None) -> dict:
    return {
        "status": "ok",
        "data": data,
        "warnings": warnings or [],
        "source_ids": source_ids or [],
        "operation_id": operation_id,
    }


def err(code: str, message: str, operation_id: str, details: Any = None) -> dict:
    return {
        "status": "error",
        "error": {"code": code, "message": message, "details": details},
        "operation_id": operation_id,
    }
