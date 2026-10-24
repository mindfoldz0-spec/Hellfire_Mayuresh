import asyncio

import pytest

from backend.app.state import CallManager, CapacityError


class DummyWS:
    def __init__(self):
        self.sent = []
        self.closed_code = None

    async def send_json(self, data):
        self.sent.append(data)

    async def close(self, code=1000):
        self.closed_code = code


async def make_call(manager, number="2007"):
    return await manager.create_call(number)


@pytest.mark.asyncio
async def test_atomic_officer_lockout():
    manager = CallManager()
    call = await make_call(manager)
    call_id = call.call_id

    caller_ws = DummyWS()
    assert await manager.register_caller(call_id, caller_ws) is True
    await manager.update_call_status(call_id, "WAITING")
    assert manager.calls[call_id].status == "WAITING"

    officer_ids = [f"OFFICER_{index:02d}" for index in range(1, 11)]

    async def try_claim(officer_id):
        return await manager.claim_call(call_id, officer_id)

    results = await asyncio.gather(*(try_claim(officer_id) for officer_id in officer_ids))
    successes = [result for result in results if result[0] is True]
    failures = [result for result in results if result[0] is False]

    assert len(successes) == 1
    assert len(failures) == 9
    assert manager.calls[call_id].officer_id in officer_ids
    assert manager.calls[call_id].status == "ENGAGED"

    late_claim, message = await manager.claim_call(call_id, "LATE_OFFICER")
    assert late_claim is False
    assert "already claimed" in message.lower()

    ended, _ = await manager.end_call(call_id, officer_id=manager.calls[call_id].officer_id)
    assert ended is True
    assert manager.calls[call_id].status == "ENDED"


@pytest.mark.asyncio
async def test_call_ids_are_unique():
    manager = CallManager()
    first = await make_call(manager)
    second = await make_call(manager)
    assert first.call_id != second.call_id
    assert len(manager.calls) == 2


@pytest.mark.asyncio
async def test_capacity_is_enforced():
    manager = CallManager()
    await manager.create_call("2007", max_active=1)
    with pytest.raises(CapacityError):
        await manager.create_call("2007", max_active=1)


@pytest.mark.asyncio
async def test_duplicate_caller_does_not_reset_or_end_the_call():
    manager = CallManager()
    call = await make_call(manager)
    call_id = call.call_id

    first_caller = DummyWS()
    second_caller = DummyWS()
    await manager.register_caller(call_id, first_caller)
    await manager.update_call_status(call_id, "WAITING")

    # Reconnecting with the same session ID replaces the socket, but must not
    # reset the call or let the stale socket terminate the new one.
    assert await manager.register_caller(call_id, second_caller) is True
    assert manager.calls[call_id].status == "WAITING"
    assert first_caller.closed_code == 4001

    await manager.unregister_caller(call_id, first_caller)
    assert manager.calls[call_id].status == "WAITING"
    assert manager.caller_sockets[call_id] is second_caller

    await manager.unregister_caller(call_id, second_caller)
    assert manager.calls[call_id].status == "ENDED"


@pytest.mark.asyncio
async def test_officer_disconnect_releases_claim():
    manager = CallManager()
    call = await make_call(manager)
    call_id = call.call_id

    caller = DummyWS()
    officer = DummyWS()
    await manager.register_caller(call_id, caller)
    await manager.register_officer("OFFICER_01", officer)
    success, _ = await manager.claim_call(call_id, "OFFICER_01")
    assert success is True
    assert manager.calls[call_id].status == "ENGAGED"

    await manager.unregister_officer("OFFICER_01", officer)
    assert manager.calls[call_id].status == "WAITING"
    assert manager.calls[call_id].officer_id is None
    assert any(message.get("event") == "CALL_RELEASED" for message in caller.sent)


@pytest.mark.asyncio
async def test_stale_officer_socket_cannot_remove_new_socket():
    manager = CallManager()
    old_socket = DummyWS()
    new_socket = DummyWS()

    await manager.register_officer("OFFICER_01", old_socket)
    await manager.register_officer("OFFICER_01", new_socket)
    assert old_socket.closed_code == 4001

    await manager.unregister_officer("OFFICER_01", old_socket)
    assert manager.officer_sockets["OFFICER_01"] is new_socket


@pytest.mark.asyncio
async def test_officer_cannot_claim_second_call():
    manager = CallManager()
    first = await make_call(manager)
    second = await make_call(manager)
    caller = DummyWS()
    await manager.register_caller(first.call_id, caller)

    success, _ = await manager.claim_call(first.call_id, "OFFICER_01")
    assert success is True

    success, reason = await manager.claim_call(second.call_id, "OFFICER_01")
    assert success is False
    assert "another call" in reason.lower()
    assert manager.calls[second.call_id].status == "RINGING"


@pytest.mark.asyncio
async def test_only_assigned_officer_can_end_call_and_end_is_idempotent():
    manager = CallManager()
    call = await make_call(manager)
    call_id = call.call_id
    caller = DummyWS()

    await manager.register_caller(call_id, caller)
    success, _ = await manager.claim_call(call_id, "OFFICER_01")
    assert success is True

    denied, _ = await manager.end_call(call_id, officer_id="OFFICER_02")
    assert denied is False
    assert manager.calls[call_id].status == "ENGAGED"

    ended, _ = await manager.end_call(call_id, officer_id="OFFICER_01")
    assert ended is True
    ended_again, _ = await manager.end_call(call_id, officer_id="OFFICER_01")
    assert ended_again is True

    end_events = [message for message in caller.sent if message.get("event") == "CALL_ENDED"]
    assert len(end_events) == 1


@pytest.mark.asyncio
async def test_caller_disconnect_ends_call_once():
    manager = CallManager()
    call = await make_call(manager)
    call_id = call.call_id
    caller = DummyWS()

    await manager.register_caller(call_id, caller)
    await manager.unregister_caller(call_id, caller)
    assert manager.calls[call_id].status == "ENDED"

    await manager.unregister_caller(call_id, caller)
    assert manager.calls[call_id].status == "ENDED"


@pytest.mark.asyncio
async def test_cleanup_removes_old_ended_calls():
    manager = CallManager()
    call = await make_call(manager)
    await manager.end_call(call.call_id, actor="caller")
    manager.calls[call.call_id].ended_at = 0

    removed = await manager.cleanup_stale_calls(ended_ttl=1)
    assert call.call_id in removed
    assert call.call_id not in manager.calls
