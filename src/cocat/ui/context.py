from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request

from ..app.actions import RoomActions
from ..app.db import DB
from ..app.users import Backend


@dataclass(frozen=True)
class UIContext:
    db: DB
    backend: Backend
    actions: RoomActions
    check_limit: Callable[[Request], None]
