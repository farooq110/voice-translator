"""Two-participant rooms (Phase 5, ADR-0014).

A room ties exactly two WebSocket connections together:

- the **host** (the translator user) creates it at `hello` and owns the
  language pair — the room's config is the host's config;
- the **guest** joins with the room's invite code and configures nothing.

The room is deliberately bookkeeping-only: it never touches audio buffers
(each connection keeps its own Session/STT pipeline) and never blocks —
all state lives in plain dicts guarded by an asyncio lock held only for
membership transitions, never across awaits on I/O.

Lifecycle
---------
- last member leaves → the room LINGERS for `linger_seconds` (reconnection
  window: a dropped client re-hellos with the same code and takes its slot
  back) and is destroyed afterwards;
- a member rejoining during the linger cancels the pending destruction;
- join fails honestly: unknown code → `room_not_found`, occupied slot →
  `room_full` (mapped to errors by the WS endpoint).
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from typing import Protocol

from ..models.protocol import HelloConfig

logger = logging.getLogger(__name__)

Role = str  # "host" | "guest"

# Unambiguous alphabet for spoken shareable codes (no I/L/O/0/1).
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


class RoomMember(Protocol):
    """What a room needs from a connection (no import of the WS module)."""

    role: Role

    async def room_send(self, payload: dict[str, object]) -> None:
        """Send one JSON event to this member (never raises for a closed
        connection — implementors swallow send errors on dead sockets)."""
        ...


class RoomFullError(RuntimeError):
    def __init__(self, role: str) -> None:
        super().__init__(f"room slot occupied: {role}")
        self.role = role


class Room:
    """One two-person session (host + optional guest)."""

    def __init__(self, code: str, linger_seconds: float) -> None:
        self.code = code
        self.linger_seconds = linger_seconds
        self.created_at = time.time()
        self.config: HelloConfig | None = None
        self.members: dict[Role, RoomMember] = {}
        self._destroy_handle: asyncio.TimerHandle | None = None

    # ------------------------------------------------------------- members

    def peer_count(self, *, excluding: Role | None = None) -> int:
        return len([r for r in self.members if r != excluding])

    def peers_of(self, role: Role) -> list[RoomMember]:
        """The members EXCLUDING `role` — the recipient set for that
        speaker's audio (ADR-0014: translated speech goes to the other
        participant, never back to the speaker)."""
        return [m for r, m in self.members.items() if r != role]

    def everyone(self) -> list[RoomMember]:
        return list(self.members.values())

    def join(self, role: Role, member: RoomMember) -> None:
        if self._destroy_handle is not None:
            self._destroy_handle.cancel()
            self._destroy_handle = None
        if role in self.members:
            raise RoomFullError(role)
        self.members[role] = member

    def leave(self, role: Role) -> None:
        self.members.pop(role, None)

    # ------------------------------------------------------------ lifecycle

    def schedule_destroy(self, manager: RoomManager) -> None:
        """Arm the linger timer — called when the last member leaves."""
        if self.members:
            return
        if self._destroy_handle is not None:
            return
        loop = asyncio.get_running_loop()
        self._destroy_handle = loop.call_later(
            self.linger_seconds, manager.destroy, self.code
        )
        logger.info(
            "room.linger code=%s linger_s=%.0f", self.code, self.linger_seconds
        )

    def cancel_destroy(self) -> None:
        if self._destroy_handle is not None:
            self._destroy_handle.cancel()
            self._destroy_handle = None


class RoomManager:
    """In-memory registry of rooms (single event loop — no thread lock)."""

    def __init__(self, linger_seconds: float = 120.0) -> None:
        self._linger_seconds = max(0.0, linger_seconds)
        self._rooms: dict[str, Room] = {}

    # ------------------------------------------------------------ queries

    def get(self, code: str | None) -> Room | None:
        if not code:
            return None
        return self._rooms.get(code)

    def stats(self) -> dict[str, int]:
        active = sum(1 for room in self._rooms.values() if room.members)
        return {"rooms": len(self._rooms), "active": active}

    # ----------------------------------------------------------- mutations

    def create(self, config: HelloConfig) -> Room:
        code = self._new_code()
        room = Room(code, self._linger_seconds)
        room.config = config
        self._rooms[code] = room
        logger.info("room.create code=%s native=%s preferred=%s",
                    code, config.native_language, config.preferred_language)
        return room

    def destroy(self, code: str) -> None:
        room = self._rooms.pop(code, None)
        if room is not None:
            room.cancel_destroy()
            logger.info("room.destroy code=%s age_s=%.0f", code, time.time() - room.created_at)

    def _new_code(self) -> str:
        while True:
            code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
            if code not in self._rooms:
                return code
