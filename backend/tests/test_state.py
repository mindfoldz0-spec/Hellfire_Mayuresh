import asyncio
import pytest
from backend.app.state import CallManager

@pytest.mark.asyncio
async def test_atomic_officer_lockout():
    manager = CallManager()
    call_id = "2007"
    
    # Simulate caller registration
    class DummyWS:
        def __init__(self):
            self.sent = []
        async def send_json(self, data):
            self.sent.append(data)

    dummy_caller_ws = DummyWS()
    await manager.register_caller(call_id, dummy_caller_ws)
    await manager.update_call_status(call_id, "WAITING")

    assert manager.calls[call_id].status == "WAITING"

    # Simulate 10 officers concurrently trying to claim call 2007
    officer_ids = [f"OFFICER_{i:02d}" for i in range(1, 11)]

    async def try_claim(off_id):
        return await manager.claim_call(call_id, off_id)

    results = await asyncio.gather(*(try_claim(off) for off in officer_ids))

    # Exactly one officer must succeed
    successes = [r for r in results if r[0] is True]
    failures = [r for r in results if r[0] is False]

    assert len(successes) == 1, f"Expected 1 successful claim, got {len(successes)}"
    assert len(failures) == 9, f"Expected 9 failed claims, got {len(failures)}"

    winning_officer = manager.calls[call_id].officer_id
    assert winning_officer in officer_ids
    assert manager.calls[call_id].status == "ENGAGED"

    # Subsequent claim attempt must also fail
    late_claim, msg = await manager.claim_call(call_id, "LATE_OFFICER")
    assert late_claim is False
    assert "already claimed" in msg.lower()

    # End call
    await manager.end_call(call_id)
    assert manager.calls[call_id].status == "ENDED"
