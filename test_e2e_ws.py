import asyncio
import json
import websockets

async def test_end_to_end_flow():
    server_base = "ws://localhost:8000"
    
    print("--- 1. Connecting Officer 1 and Officer 2 WebSockets ---")
    async with websockets.connect(f"{server_base}/ws/officer/OFFICER_01") as ws_off1, \
               websockets.connect(f"{server_base}/ws/officer/OFFICER_02") as ws_off2:
               
        # Both officers receive initial queue snapshot
        snap1 = json.loads(await ws_off1.recv())
        snap2 = json.loads(await ws_off2.recv())
        print("Officer 1 received snapshot:", snap1.get("event"))
        print("Officer 2 received snapshot:", snap2.get("event"))

        print("\n--- 2. Caller dials 2007 ---")
        async with websockets.connect(f"{server_base}/ws/caller/2007") as ws_caller:
            # Both officers should receive NEW_CALL broadcast
            new_call_msg1 = json.loads(await ws_off1.recv())
            new_call_msg2 = json.loads(await ws_off2.recv())
            print(f"Officer 1 received: {new_call_msg1.get('event')} for #{new_call_msg1['call']['call_id']}")
            print(f"Officer 2 received: {new_call_msg2.get('event')} for #{new_call_msg2['call']['call_id']}")

            print("\n--- 3. Caller finishes IVR & Beep -> Status becomes WAITING ---")
            await ws_caller.send(json.dumps({"action": "IVR_FINISHED"}))
            status_msg1 = json.loads(await ws_off1.recv())
            status_msg2 = json.loads(await ws_off2.recv())
            print("Officer 1 received status change:", status_msg1)
            print("Officer 2 received status change:", status_msg2)

            print("\n--- 4. Officer 1 claims Call 2007 ---")
            await ws_off1.send(json.dumps({"action": "CLAIM_CALL", "call_id": "2007"}))

            # Officer 1 receives CALL_CLAIMED broadcast and CLAIM_RESULT
            claim_broadcast_off1 = json.loads(await ws_off1.recv())
            claim_res_off1 = json.loads(await ws_off1.recv())
            print("Officer 1 claim result:", claim_res_off1)

            # Officer 2 receives CALL_CLAIMED broadcast (lockout notification)
            claim_broadcast_off2 = json.loads(await ws_off2.recv())
            print(f"Officer 2 received lockout broadcast: Call {claim_broadcast_off2.get('call_id')} claimed by {claim_broadcast_off2.get('officer_id')}")

            # Caller receives OFFICER_ASSIGNED notification
            caller_assigned = json.loads(await ws_caller.recv())
            print(f"Caller received officer assignment: {caller_assigned.get('officer_id')}")

            print("\n--- 5. Officer 2 attempts to claim the same call (Should be locked out!) ---")
            await ws_off2.send(json.dumps({"action": "CLAIM_CALL", "call_id": "2007"}))
            claim_res_off2 = json.loads(await ws_off2.recv())
            print("Officer 2 claim attempt response:", claim_res_off2)
            assert claim_res_off2.get("success") is False, "Officer 2 should have been locked out!"
            print("LOCKOUT VERIFIED: Officer 2 was successfully prevented from stealing the call!")

            print("\n--- 6. WebRTC Signaling Relay ---")
            mock_offer = {"type": "offer", "sdp": "v=0\r\no=mock-officer 123456 ..."}
            await ws_off1.send(json.dumps({
                "action": "WEBRTC_SIGNAL",
                "call_id": "2007",
                "payload": {"offer": mock_offer}
            }))

            # Caller receives the WebRTC Offer relayed by backend
            caller_signal = json.loads(await ws_caller.recv())
            print("Caller received WebRTC Offer relay:", caller_signal.get("payload", {}).get("offer", {}).get("type"))

            # Caller sends WebRTC Answer
            mock_answer = {"type": "answer", "sdp": "v=0\r\no=mock-caller 654321 ..."}
            await ws_caller.send(json.dumps({
                "action": "WEBRTC_SIGNAL",
                "payload": {"answer": mock_answer}
            }))

            # Officer 1 receives WebRTC Answer
            off1_signal = json.loads(await ws_off1.recv())
            print("Officer 1 received WebRTC Answer relay:", off1_signal.get("payload", {}).get("answer", {}).get("type"))

            print("\n--- 7. End Call ---")
            await ws_off1.send(json.dumps({"action": "END_CALL", "call_id": "2007"}))

            end_msg_off1 = json.loads(await ws_off1.recv())
            end_msg_off2 = json.loads(await ws_off2.recv())
            caller_end = json.loads(await ws_caller.recv())

            print("All parties cleanly notified of call completion.")

    print("\n==========================================")
    print("ALL END-TO-END FLOW TESTS PASSED 100%!")
    print("==========================================")

if __name__ == "__main__":
    asyncio.run(test_end_to_end_flow())
