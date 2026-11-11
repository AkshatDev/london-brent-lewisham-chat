"""Chat sessions in memory, so follow-up questions build on earlier answers.

A session keeps its recent turns (question, tool calls with their results, answer)
as Responses API input items, ready to send again with the next question. That is
what lets "how many of those were refused?" know what "those" were.

Everything is bounded: turns per session, idle time, number of sessions. Sessions
live in this process only and are lost on restart (said in the README).
"""

import asyncio
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

Item = dict[str, Any]
"""One Responses API input item, e.g. ``{"role": "user", "content": "..."}``."""


@dataclass
class Session:
    """One conversation: its recent turns, citation numbering and a lock."""

    id: str
    turns: list[list[Item]] = field(default_factory=list)
    """Each turn's items, oldest first; a turn is kept or dropped whole, so a tool
    call never loses its result."""
    citations_issued: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    """Held while a question is answered, so two requests in one session can't
    interleave their history."""

    def history(self) -> list[Item]:
        """Return the kept turns as one list of input items, oldest first.

        Returns:
            The items to send before the new question.
        """
        return [item for turn in self.turns for item in turn]

    def add_turn(self, items: list[Item], max_turns: int) -> None:
        """Keep a finished turn, dropping the oldest beyond ``max_turns``.

        Args:
            items: The turn's items (question, tool calls and results, answer).
            max_turns: Most turns kept.
        """
        self.turns.append(items)
        del self.turns[:-max_turns]

    def next_citation_id(self) -> str:
        """Return the next citation id: ``c1``, ``c2``, ... across the session.

        Numbered per session, not per question, because a later answer may still
        refer to an earlier ``[c1]``.

        Returns:
            The new id.
        """
        self.citations_issued += 1
        return f"c{self.citations_issued}"


class SessionStore:
    """All sessions, least recently used first; idle ones expire, the oldest go."""

    def __init__(
        self,
        idle_ttl_s: float,
        max_count: int,
        max_turns: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create an empty store.

        Args:
            idle_ttl_s: Seconds a session may go unused before it is forgotten.
            max_count: Most sessions kept; the least recently used goes first.
            max_turns: Most turns kept per session (passed to ``Session.add_turn``).
            clock: Seconds from a steady clock; tests pass a fake one.
        """
        self.max_turns = max_turns
        self._idle_ttl_s = idle_ttl_s
        self._max_count = max_count
        self._clock = clock
        self._sessions: OrderedDict[str, tuple[float, Session]] = OrderedDict()

    def get(self, session_id: str) -> Session:
        """Return the session with this id, starting a new one if none is live.

        Using a session marks it as recently used. Expired sessions are removed
        here (they are oldest first, so this stops at the first live one), and the
        least recently used are dropped beyond ``max_count``. A request already
        holding a dropped session finishes normally; its turn is simply not kept.

        Args:
            session_id: The id the client sent.

        Returns:
            The session, new or continued.
        """
        now = self._clock()
        while self._sessions:
            oldest_id, (last_used, _) = next(iter(self._sessions.items()))
            if now - last_used < self._idle_ttl_s:
                break
            del self._sessions[oldest_id]

        _, session = self._sessions.pop(session_id, (now, None))
        if session is None:
            session = Session(id=session_id)
        self._sessions[session_id] = (now, session)
        while len(self._sessions) > self._max_count:
            self._sessions.popitem(last=False)
        return session

    def __len__(self) -> int:
        """Return how many sessions are kept.

        Returns:
            The number of sessions.
        """
        return len(self._sessions)
