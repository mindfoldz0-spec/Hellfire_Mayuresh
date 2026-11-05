import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from backend.app import recordings as recordings_module
from backend.app.main import OFFICER_TOKEN, app
from backend.app.recordings import _sync_from_supabase_sync


def login(client, token=OFFICER_TOKEN, officer_id="OFFICER_01"):
    return client.post("/api/auth/login", json={"token": token, "officer_id": officer_id})


def test_list_recordings_authenticated():
    with TestClient(app) as client:
        login(client)
        resp = client.get("/api/recordings")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["items"], list)
        assert data["total"] == len(data["items"])


def test_sync_from_supabase_with_mock():
    mock_supabase = MagicMock()
    mock_supabase.storage.from_().list.return_value = [
        {
            "name": "recording_1792855139085_test123.webm",
            "metadata": {"size": 1024},
            "created_at": "2026-10-24T15:19:05Z",
        }
    ]
    mock_supabase.storage.from_().get_public_url.return_value = (
        "https://mock.supabase.co/recording_test.webm"
    )

    with patch("supabase.create_client", return_value=mock_supabase), patch(
        "backend.app.recordings.SUPABASE_URL", "https://mock.supabase.co"
    ), patch("backend.app.recordings.SUPABASE_KEY", "mock-key"):
        _sync_from_supabase_sync(force=True)
        mock_supabase.storage.from_().list.assert_called()


def test_sync_paginates_until_a_short_page():
    page = [
        {"name": f"recording_{i}_abc.webm", "metadata": {"size": 1}}
        for i in range(100)
    ]
    mock_supabase = MagicMock()
    mock_supabase.storage.from_().list.side_effect = [page, page[:10]]
    mock_supabase.storage.from_().get_public_url.return_value = "https://x/y.webm"

    with patch("supabase.create_client", return_value=mock_supabase), patch(
        "backend.app.recordings.SUPABASE_URL", "https://mock.supabase.co"
    ), patch("backend.app.recordings.SUPABASE_KEY", "mock-key"):
        _sync_from_supabase_sync(force=True)
        assert mock_supabase.storage.from_().list.call_count >= 2


def test_analyze_recording_requires_auth():
    with TestClient(app) as client:
        resp = client.post("/api/recordings/nonexistent/analyze")
        assert resp.status_code == 401


def test_analyze_recording_not_found():
    with TestClient(app) as client:
        login(client)
        resp = client.post("/api/recordings/recording_nonexistent.json/analyze")
        assert resp.status_code == 404


def test_analyze_recording_success():
    """The dossier the API returns must use the canonical schema.

    This mock mirrors ``backend.app.ai._normalize_forensics`` exactly; the
    previous fixture still used the pre-refactor field names and therefore
    validated the frontend against itself.
    """
    mock_forensics = {
        "analyzed_at": 1792900000,
        "caller_dossier": {
            "name": "Ramesh Patil",
            "contact": "9876543210",
            "location": "Pune",
            "language_spoken": "Marathi / English",
        },
        "suspect_profile": {
            "claimed_identity": "Inspector R.K. Sharma",
            "impersonated_agency": "Mumbai Crime Branch",
            "phone_numbers": ["+91 99887 76655"],
            "digital_handles": ["@crimebranch_investigate"],
        },
        "financial_impact": {
            "amount_lost": "₹2,50,000",
            "amount_demanded": "₹5,00,000",
            "destination_upi_ids": ["cbi-verify@oksbi"],
            "destination_bank_accounts": [],
            "victim_bank": "State Bank of India",
        },
        "attack_vector": {
            "scam_type": "Digital Arrest Extortion",
            "coercion_technique": "Threatened seizure of passport and arrest warrant",
            "malicious_tools": ["AnyDesk", "CBI-Safety.apk"],
        },
        "key_facts_timeline": [
            "Caller received Skype call from suspect posing as police officer.",
            "Victim coerced into transferring Rs 2,50,000 as clearing bond.",
        ],
        "actionable_investigation_sop": [
            "Initiate immediate 1930 portal ticket to freeze destination UPI cbi-verify@oksbi.",
            "Issue 91 CrPC notice to telecom provider for +91 99887 76655.",
        ],
        "psychological_evaluation": {
            "dominant_state": "Acute Panic",
            "summary": "Victim displays acute distress from state-agency impersonation.",
            "vulnerability_level": "CRITICAL",
        },
        "triage_verdict": {
            "priority_score": 96,
            "urgency_badge": "🚨 VERY VERY URGENT",
            "category": "DIGITAL ARREST EXTORTION",
            "vulnerability_assessment": "CRITICAL",
        },
        "emotions": {
            "dominant": "fear",
            "fear": 0.88,
            "distress": 0.76,
            "anger": 0.12,
            "nervousness": 0.64,
            "neutral": 0.08,
            "vocal_distress_score": 0.88,
            "source": "aws_ser",
        },
        "responsible_ai_advisory": "advisory",
    }

    import json

    from backend.app.recordings import RECORDINGS_DIR

    test_meta_name = "recording_test_forensics.json"
    test_meta_file = RECORDINGS_DIR / test_meta_name
    dummy_rec = {
        "call_id": "test_forensics",
        "meta_name": test_meta_name,
        "filename": "test_forensics.webm",
        "timestamp": 1729780000,
        "duration": 25.0,
        "notes": "Victim reported Rs 2.5 lakh extorted via Skype video call.",
        "transcript": "They said they are police and forced me to transfer money.",
    }

    test_meta_file.write_text(json.dumps(dummy_rec), encoding="utf-8")
    with TestClient(app) as client:
        login(client)
        with patch(
            "backend.app.recordings.analyze_call_forensics", return_value=mock_forensics
        ):
            resp = client.post(f"/api/recordings/{test_meta_name}/analyze")
            assert resp.status_code == 200
            data = resp.json()
            assert data["caller_dossier"]["name"] == "Ramesh Patil"
            assert data["caller_dossier"]["contact"] == "9876543210"
            assert data["financial_impact"]["amount_lost"] == "₹2,50,000"
            assert data["emotions"]["dominant"] == "fear"
            assert data["triage_verdict"]["priority_score"] == 96
            assert data["triage_verdict"]["category"] == "DIGITAL ARREST EXTORTION"

            # Verify cached on disk
            saved = json.loads(test_meta_file.read_text(encoding="utf-8"))
            assert "forensic_analysis" in saved
            assert saved["forensic_analysis"]["caller_dossier"]["name"] == "Ramesh Patil"


def test_delete_recording_removes_metadata_and_audio():
    import json

    from backend.app.recordings import RECORDINGS_DIR

    meta_name = "recording_delete_me.json"
    audio_name = "recording_delete_me.webm"
    (RECORDINGS_DIR / meta_name).write_text(
        json.dumps({"filename": audio_name, "timestamp": 1}), encoding="utf-8"
    )
    (RECORDINGS_DIR / audio_name).write_bytes(b"RIFF0000WAVE")

    with TestClient(app) as client:
        assert client.delete(f"/api/recordings/{meta_name}").status_code == 401
        login(client)
        resp = client.delete(f"/api/recordings/{meta_name}")
        assert resp.status_code == 200
        assert not (RECORDINGS_DIR / meta_name).exists()
        assert not (RECORDINGS_DIR / audio_name).exists()


def test_listing_flags_metadata_without_audio():
    import json

    from backend.app.recordings import RECORDINGS_DIR

    (RECORDINGS_DIR / "recording_orphan.json").write_text(
        json.dumps(
            {
                "filename": "recording_orphan.webm",
                "timestamp": 5,
                "url": "/api/recordings/recording_orphan.webm",
            }
        ),
        encoding="utf-8",
    )

    with TestClient(app) as client:
        login(client)
        rows = client.get("/api/recordings").json()["items"]
        orphan = next(r for r in rows if r["meta_name"] == "recording_orphan.json")
        assert orphan["audio_available"] is False
        assert "url" not in orphan


# ---------------------------------------------------------------------------
# Dual-channel capture, evidence integrity, case packet, retention
# ---------------------------------------------------------------------------
@contextmanager
def claimed_call(client, officer_id="OFFICER_01"):
    """Create a call and have an officer claim it, keeping the socket open."""
    created = client.post("/api/calls", json={"dialed_number": "2007"})
    call_id = created.json()["call_id"]
    with client.websocket_connect(f"/ws/officer/{officer_id}") as ws:
        assert ws.receive_json()["event"] == "QUEUE_SNAPSHOT"
        ws.send_json({"action": "CLAIM_CALL", "call_id": call_id})
        events = {ws.receive_json()["event"], ws.receive_json()["event"]}
        assert "CLAIM_RESULT" in events
        yield call_id


def _upload(client, call_id, client_uuid="", notes="dual"):
    return client.post(
        f"/api/recordings/{call_id}",
        files={
            "audio_file": ("mixed.webm", b"RIFF" + b"m" * 64, "audio/webm"),
            "officer_audio": ("off.webm", b"RIFF" + b"o" * 64, "audio/webm"),
            "caller_audio": ("cal.webm", b"RIFF" + b"c" * 64, "audio/webm"),
        },
        data={"duration": "12.5", "notes": notes, "client_uuid": client_uuid},
    )


def _meta(path_name):
    return json.loads(
        (recordings_module.RECORDINGS_DIR / path_name).read_text(encoding="utf-8")
    )


def test_upload_stores_dual_channel_tracks_with_hashes():
    with TestClient(app) as client:
        login(client)
        with claimed_call(client) as call_id:
            response = _upload(client, call_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body["tracks"]) == {"mixed", "officer", "caller"}

    meta = _meta(body["meta_name"])
    assert set(meta["sha256"]) == {"mixed", "officer", "caller"}
    assert meta["chain"]["hash"]
    for filename in meta["tracks"].values():
        assert (recordings_module.RECORDINGS_DIR / filename).is_file()


def test_upload_is_idempotent_by_client_uuid():
    with TestClient(app) as client:
        login(client)
        with claimed_call(client) as call_id:
            first = _upload(client, call_id, client_uuid="same-uuid")
            second = _upload(client, call_id, client_uuid="same-uuid")
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    assert second.json()["meta_name"] == first.json()["meta_name"]


def test_detail_returns_full_record_and_track_urls():
    with TestClient(app) as client:
        login(client)
        with claimed_call(client) as call_id:
            meta_name = _upload(client, call_id).json()["meta_name"]
        detail = client.get(f"/api/recordings/{meta_name}/detail").json()
    assert detail["meta_name"] == meta_name
    assert set(detail["tracks"]) == {"mixed", "officer", "caller"}
    assert detail["track_urls"]["caller"].startswith("/api/recordings/")


def test_verify_detects_audio_tampering():
    with TestClient(app) as client:
        login(client)
        with claimed_call(client) as call_id:
            meta_name = _upload(client, call_id).json()["meta_name"]

        assert client.get(f"/api/recordings/{meta_name}/verify").json()["intact"] is True

        meta = _meta(meta_name)
        tampered = recordings_module.RECORDINGS_DIR / meta["tracks"]["mixed"]
        tampered.write_bytes(b"tampered" * 10)

        report = client.get(f"/api/recordings/{meta_name}/verify").json()
    assert report["tracks"]["mixed"] == "MISMATCH"
    assert report["intact"] is False


def test_list_paginates_and_searches():
    with TestClient(app) as client:
        login(client)
        for index in range(3):
            (recordings_module.RECORDINGS_DIR / f"recording_page{index}.json").write_text(
                json.dumps(
                    {
                        "id": f"page{index}",
                        "meta_name": f"recording_page{index}.json",
                        "timestamp": 1000 + index,
                        "notes": f"needle-{index}",
                        "filename": f"recording_page{index}.webm",
                    }
                ),
                encoding="utf-8",
            )
        page = client.get("/api/recordings?limit=1&offset=0").json()
        assert page["total"] == 3
        assert len(page["items"]) == 1
        assert page["has_more"] is True

        only_two = client.get("/api/recordings?q=needle-2").json()
    assert only_two["total"] == 1
    assert only_two["items"][0]["id"] == "page2"


def test_update_disposition_and_reject_unknown():
    with TestClient(app) as client:
        login(client)
        with claimed_call(client) as call_id:
            meta_name = _upload(client, call_id).json()["meta_name"]

        ok = client.put(
            f"/api/recordings/{meta_name}",
            json={"notes": "closed", "disposition": "resolved"},
        )
        assert ok.status_code == 200
        assert ok.json()["disposition"] == "RESOLVED"

        bad = client.put(
            f"/api/recordings/{meta_name}",
            json={"notes": "x", "disposition": "banana"},
        )
    assert bad.status_code == 400


def test_case_packet_pdf_renders():
    with TestClient(app) as client:
        login(client)
        with claimed_call(client) as call_id:
            meta_name = _upload(client, call_id).json()["meta_name"]

        path = recordings_module.RECORDINGS_DIR / meta_name
        meta = json.loads(path.read_text(encoding="utf-8"))
        meta["forensic_analysis"] = {
            "caller_dossier": {
                "name": "Ramesh Patil",
                "contact": "9876543210",
                "location": "Pune",
                "language_spoken": "Marathi",
            },
            "suspect_profile": {
                "claimed_identity": "CBI Officer",
                "impersonated_agency": "CBI",
                "phone_numbers": ["+91 99887 76655"],
                "digital_handles": [],
            },
            "financial_impact": {
                "amount_lost": "?2,50,000",
                "amount_demanded": "?5,00,000",
                "destination_upi_ids": ["fraud@icici"],
                "destination_bank_accounts": [],
                "victim_bank": "SBI",
            },
            "attack_vector": {
                "scam_type": "Digital Arrest",
                "coercion_technique": "Arrest threat",
                "malicious_tools": ["AnyDesk"],
            },
            "key_facts_timeline": ["Caller received a video call from the suspect."],
            "actionable_investigation_sop": ["File a 1930 portal ticket."],
            "psychological_evaluation": {
                "dominant_state": "Acute Panic",
                "summary": "Severe distress throughout.",
                "vulnerability_level": "CRITICAL",
            },
            "triage_verdict": {
                "priority_score": 96,
                "urgency_badge": "?? VERY VERY URGENT",
                "category": "DIGITAL ARREST EXTORTION",
                "vulnerability_assessment": "CRITICAL",
            },
            "emotions": {
                "fear": 0.9,
                "distress": 0.8,
                "anger": 0.1,
                "nervousness": 0.7,
                "neutral": 0.05,
                "source": "aws_ser",
                "vocal_distress_score": 0.86,
            },
            "responsible_ai_advisory": "advisory",
        }
        path.write_text(json.dumps(meta), encoding="utf-8")

        response = client.get(f"/api/recordings/{meta_name}/packet.pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content[:4] == b"%PDF"
    assert len(response.content) > 2000


def test_retention_purge_removes_old_records():
    import time

    directory = recordings_module.RECORDINGS_DIR
    old_audio = directory / "recording_old.webm"
    old_audio.write_bytes(b"RIFF" + b"x" * 32)
    (directory / "recording_old.json").write_text(
        json.dumps(
            {
                "id": "old",
                "meta_name": "recording_old.json",
                "timestamp": int(time.time()) - 40 * 86_400,
                "filename": "recording_old.webm",
                "tracks": {"mixed": "recording_old.webm"},
            }
        ),
        encoding="utf-8",
    )

    removed = recordings_module.purge_expired(30)
    assert "recording_old.json" in removed
    assert not (directory / "recording_old.json").exists()
    assert not old_audio.exists()
