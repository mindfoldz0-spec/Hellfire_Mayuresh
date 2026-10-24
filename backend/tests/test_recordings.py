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
