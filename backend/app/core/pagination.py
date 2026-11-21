"""Opaque keyset-pagination cursors over (created_at, id), newest first.

Keyset (not OFFSET) pagination stays fast on large tables and is stable under concurrent inserts.
"""

import base64
import json
import uuid
from datetime import datetime

from app.core.errors import AppError


class InvalidCursorError(AppError):
    status_code = 400
    problem_type = "invalid-cursor"
    title = "Invalid Cursor"


def encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    raw = json.dumps({"t": created_at.isoformat(), "id": str(row_id)}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        return datetime.fromisoformat(data["t"]), uuid.UUID(data["id"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise InvalidCursorError("The pagination cursor is malformed.") from exc
