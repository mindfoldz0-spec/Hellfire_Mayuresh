import os
import json
import time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from backend.app.state import call_manager

app = FastAPI(title="Cyber Emergency Response System (CERS)")

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure directories exist
RECORDINGS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "recordings")
os.makedirs(RECORDINGS_DIR, exist_ok=True)
os.makedirs("frontend/static", exist_ok=True)
os.makedirs("frontend/caller", exist_ok=True)
os.makedirs("frontend/officer", exist_ok=True)

# Mount recordings and static folders
app.mount("/static", StaticFiles(directory="frontend/static"), name="static")
app.mount("/recordings", StaticFiles(directory=RECORDINGS_DIR), name="recordings")

# Health check
@app.get("/health")
async def health():
    return {"status": "ok"}

# Serve UI HTML Pages
@app.get("/")
@app.get("/caller")
@app.get("/call")
async def get_caller_page():
    return FileResponse("frontend/caller/index.html")

@app.get("/caller/{filename}")
async def get_caller_static(filename: str):
    path = os.path.join("frontend", "caller", filename)
    if os.path.exists(path):
        return FileResponse(path)
    return JSONResponse({"error": "File not found"}, status_code=404)

@app.get("/officer")
async def get_officer_page():
    return FileResponse("frontend/officer/index.html")

@app.get("/officer/{filename}")
async def get_officer_static(filename: str):
    path = os.path.join("frontend", "officer", filename)
    if os.path.exists(path):
        return FileResponse(path)
    return JSONResponse({"error": "File not found"}, status_code=404)

# Recording API Endpoints
@app.post("/api/recordings/{call_id}")
async def upload_recording(
    call_id: str,
    audio_file: UploadFile = File(...),
    duration: float = Form(0.0),
    notes: str = Form("")
):
    timestamp = int(time.time())
    ext = os.path.splitext(audio_file.filename)[1] or ".webm"
    save_filename = f"call_{call_id}_{timestamp}{ext}"
    save_path = os.path.join(RECORDINGS_DIR, save_filename)

    with open(save_path, "wb") as f:
        content = await audio_file.read()
        f.write(content)

    recording_url = f"/recordings/{save_filename}"
    
    # Update call state if exists
    if call_id in call_manager.calls:
        call = call_manager.calls[call_id]
        call.recording_url = recording_url
        if notes:
            call.notes = notes

    # Save metadata JSON file
    meta_path = os.path.join(RECORDINGS_DIR, f"call_{call_id}_{timestamp}.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({
            "call_id": call_id,
            "filename": save_filename,
            "url": recording_url,
            "timestamp": timestamp,
            "duration": duration,
            "notes": notes,
            "size_bytes": len(content)
        }, f, indent=2)

    return JSONResponse({
        "status": "success",
        "url": recording_url,
        "filename": save_filename
    })

@app.get("/api/recordings")
async def list_recordings():
    recordings = []
    if os.path.exists(RECORDINGS_DIR):
        for f in os.listdir(RECORDINGS_DIR):
            if f.endswith(".json"):
                try:
                    with open(os.path.join(RECORDINGS_DIR, f), "r", encoding="utf-8") as meta_f:
                        recordings.append(json.load(meta_f))
                except Exception:
                    pass
    # Sort descending by timestamp
    recordings.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
    return JSONResponse(recordings)

# WebSocket: Caller Endpoint
@app.websocket("/ws/caller/{call_id}")
async def caller_websocket(websocket: WebSocket, call_id: str):
    await websocket.accept()
    await call_manager.register_caller(call_id, websocket)
    print(f"[WS] Caller connected: {call_id}")

    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action")

            if action == "IVR_FINISHED":
                # The beep tone has concluded; caller is waiting for an officer
                await call_manager.update_call_status(call_id, "WAITING")

            elif action == "WEBRTC_SIGNAL":
                # Forward SDP Answer or ICE Candidate to the engaged officer
                call = call_manager.calls.get(call_id)
                if call and call.officer_id:
                    await call_manager.relay_webrtc("officer", call.officer_id, data.get("payload"))

            elif action == "END_CALL":
                await call_manager.end_call(call_id)
                break

    except WebSocketDisconnect:
        print(f"[WS] Caller disconnected: {call_id}")
    except Exception as e:
        print(f"[WS] Error in caller socket {call_id}: {e}")
    finally:
        await call_manager.unregister_caller(call_id)

# WebSocket: Officer Control Room Endpoint
@app.websocket("/ws/officer/{officer_id}")
async def officer_websocket(websocket: WebSocket, officer_id: str):
    await websocket.accept()
    await call_manager.register_officer(officer_id, websocket)
    print(f"[WS] Officer connected: {officer_id}")

    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action")
            call_id = data.get("call_id")

            if action == "CLAIM_CALL":
                success, reason = await call_manager.claim_call(call_id, officer_id)
                await websocket.send_json({
                    "event": "CLAIM_RESULT",
                    "call_id": call_id,
                    "success": success,
                    "reason": reason
                })

            elif action == "WEBRTC_SIGNAL":
                # Forward SDP Offer or ICE candidate from officer to caller
                if call_id:
                    await call_manager.relay_webrtc("caller", call_id, data.get("payload"))

            elif action == "END_CALL":
                if call_id:
                    await call_manager.end_call(call_id, officer_id)

    except WebSocketDisconnect:
        print(f"[WS] Officer disconnected: {officer_id}")
    except Exception as e:
        print(f"[WS] Error in officer socket {officer_id}: {e}")
    finally:
        await call_manager.unregister_officer(officer_id)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8000, reload=True)
