from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

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
        assert isinstance(data, list)


def test_sync_from_supabase_with_mock():
    mock_supabase = MagicMock()
    mock_supabase.storage.from_().list.return_value = [
        {
            "name": "recording_1792855139085_test123.webm",
            "metadata": {"size": 1024},
            "created_at": "2026-10-24T15:19:05Z",
        }
    ]
    mock_supabase.storage.from_().get_public_url.return_value = "https://mock.supabase.co/recording_test.webm"

    with patch("supabase.create_client", return_value=mock_supabase), \
         patch("backend.app.recordings.SUPABASE_URL", "https://mock.supabase.co"), \
         patch("backend.app.recordings.SUPABASE_KEY", "mock-key"):
        _sync_from_supabase_sync(force=True)
        # Verify list was called
        mock_supabase.storage.from_().list.assert_called()


def test_analyze_recording_requires_auth():
    with TestClient(app) as client:
        resp = client.post("/api/recordings/nonexistent/analyze")
        assert resp.status_code == 401


def test_analyze_recording_not_found():
    with TestClient(app) as client:
        login(client)
        resp = client.post("/api/recordings/recording_nonexistent.json/analyze")
        assert resp.status_code == 404


def test_analyze_recording_success(tmp_path):
    mock_forensics = {
        "caller_dossier": {
            "name": "Ramesh Patil",
            "contact_number": "9876543210",
            "location_city": "Pune",
            "spoken_language": "Marathi / English",
        },
        "suspect_profile": {
            "claimed_identity": "Inspector R.K. Sharma",
            "claimed_agency": "Mumbai Crime Branch",
            "phone_numbers": ["+91 99887 76655"],
            "digital_handles": ["@crimebranch_investigate"],
        },
        "financial_impact": {
            "amount_lost_inr": "₹2,50,000",
            "amount_demanded_inr": "₹5,00,000",
            "destination_upi_ids": ["cbi-verify@oksbi"],
            "victim_bank_name": "State Bank of India",
        },
        "attack_vector": {
            "scam_type": "Digital Arrest Extortion",
            "coercion_mechanism": "Threatened seizure of passport and arrest warrant",
            "malicious_tools_or_apks": ["AnyDesk", "CBI-Safety.apk"],
        },
        "key_facts_timeline": [
            "Caller received Skype call from suspect posing as police officer.",
            "Suspect claimed parcel in victim name contained illicit narcotics.",
            "Victim coerced into transferring Rs 2,50,000 as clearing bond.",
        ],
        "actionable_investigation_sop": [
            "Initiate immediate 1930 portal ticket to freeze destination UPI cbi-verify@oksbi.",
            "Issue 91 CrPC notice to telecom provider for +91 99887 76655.",
            "Instruct victim to factory-reset device to purge AnyDesk/APKs.",
        ],
        "emotions": {
            "dominant": "fear",
            "fear": 0.88,
            "distress": 0.76,
            "anger": 0.12,
            "nervousness": 0.64,
            "neutral": 0.08,
            "distress_score": 88,
        },
        "psychological_evaluation": "Victim displays acute distress and terror from state-agency impersonation.",
        "triage_verdict": {
            "category": "DIGITAL ARREST EXTORTION",
            "urgency": "CRITICAL",
            "priority_score": 96,
            "vulnerability_assessment": "CRITICAL VULNERABILITY",
        },
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

    try:
        test_meta_file.write_text(json.dumps(dummy_rec), encoding="utf-8")
        with TestClient(app) as client:
            login(client)
            with patch("backend.app.recordings.analyze_call_forensics", return_value=mock_forensics):
                resp = client.post(f"/api/recordings/{test_meta_name}/analyze")
                assert resp.status_code == 200
                data = resp.json()
                assert data["caller_dossier"]["name"] == "Ramesh Patil"
                assert data["financial_impact"]["amount_lost_inr"] == "₹2,50,000"
                assert data["emotions"]["dominant"] == "fear"
                assert data["triage_verdict"]["priority_score"] == 96

                # Verify cached on disk
                saved = json.loads(test_meta_file.read_text(encoding="utf-8"))
                assert "forensic_analysis" in saved
                assert saved["forensic_analysis"]["caller_dossier"]["name"] == "Ramesh Patil"
    finally:
        if test_meta_file.exists():
            test_meta_file.unlink()

