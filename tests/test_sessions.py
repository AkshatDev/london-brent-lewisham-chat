"""In-memory sessions: follow-up history, citation numbering, bounded size and life."""

import asyncio

from london_brent_lewisham_chat.sessions import SessionStore

TTL_S = 60.0


class FakeClock:
    """A steady clock the test moves by hand, so expiry needs no sleeping."""

    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds


def make_store(
    max_count: int = 10, max_turns: int = 3
) -> tuple[SessionStore, FakeClock]:
    clock = FakeClock()
    return SessionStore(TTL_S, max_count, max_turns, clock=clock), clock


def user(text: str) -> dict[str, str]:
    return {"role": "user", "content": text}


def test_the_same_id_returns_the_same_session_with_its_history() -> None:
    store, _ = make_store()
    turn = [user("How many in 2024?"), {"role": "assistant", "content": "3,140 [c1]"}]

    store.get("demo").add_turn(turn, store.max_turns)

    assert store.get("demo").history() == turn


def test_different_ids_are_separate_conversations() -> None:
    store, _ = make_store()
    store.get("a").add_turn([user("about a")], store.max_turns)

    assert store.get("b").history() == []


def test_only_the_last_turns_are_kept_and_whole() -> None:
    store, _ = make_store(max_turns=2)
    session = store.get("demo")
    call = {"type": "function_call", "call_id": "x", "name": "count_applications"}
    result = {"type": "function_call_output", "call_id": "x", "output": "{}"}

    session.add_turn([user("q1")], store.max_turns)
    session.add_turn([user("q2"), call, result], store.max_turns)
    session.add_turn([user("q3")], store.max_turns)

    # q1 dropped whole; q2's tool call kept together with its result.
    assert session.history() == [user("q2"), call, result, user("q3")]


def test_citation_ids_continue_across_the_session() -> None:
    store, _ = make_store()
    session = store.get("demo")

    first_turn = [session.next_citation_id(), session.next_citation_id()]
    second_turn = [store.get("demo").next_citation_id()]

    assert first_turn + second_turn == ["c1", "c2", "c3"]
    assert store.get("other").next_citation_id() == "c1"


def test_a_session_idle_too_long_starts_again() -> None:
    store, clock = make_store()
    store.get("demo").add_turn([user("q1")], store.max_turns)

    clock.seconds += TTL_S - 1
    assert store.get("demo").history() == [user("q1")]  # used again: kept

    clock.seconds += TTL_S
    assert store.get("demo").history() == []  # idle for the full TTL: forgotten


def test_expired_sessions_are_removed_not_just_hidden() -> None:
    store, clock = make_store()
    for session_id in ("a", "b", "c"):
        store.get(session_id)

    clock.seconds += TTL_S
    store.get("d")

    assert len(store) == 1


def test_the_least_recently_used_session_goes_when_full() -> None:
    store, _ = make_store(max_count=2)
    store.get("a").add_turn([user("about a")], store.max_turns)
    store.get("b").add_turn([user("about b")], store.max_turns)
    store.get("a")  # a is now the most recently used
    store.get("c")  # full: b goes

    assert len(store) == 2
    assert store.get("a").history() == [user("about a")]
    assert store.get("b").history() == []  # b was dropped and starts again


def test_a_second_request_in_a_session_waits_for_the_first() -> None:
    store, _ = make_store()
    order: list[str] = []

    async def ask(name: str, pause: float) -> None:
        session = store.get("demo")
        async with session.lock:
            order.append(f"{name} start")
            await asyncio.sleep(pause)
            order.append(f"{name} end")

    async def both() -> None:
        await asyncio.gather(ask("first", 0.02), ask("second", 0))

    asyncio.run(both())

    assert order == ["first start", "first end", "second start", "second end"]
