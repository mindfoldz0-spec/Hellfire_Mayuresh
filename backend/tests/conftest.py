"""Pytest configuration: deterministic auth values and offline defaults."""

from __future__ import annotations

import os

import pytest

# Set before importing application modules: config.py refuses to start without
# an explicit officer credential.
os.environ.setdefault("CERS_OFFICER_TOKEN", "test-officer-token-0123456789abcdef")
os.environ.setdefault("CERS_OFFICER_PASSWORD", "test-officer-password-0123456789abcd")
# Pin shared-token mode so a developer's real .env CERS_OFFICERS cannot flip
# the offline suite into per-officer mode. (load_dotenv does not override
# variables already present, so this empty value wins over .env.)
os.environ.setdefault("CERS_OFFICERS", "")
os.environ.setdefault("CERS_SECRET_KEY", "test-session-secret-0123456789abcdef")
os.environ.setdefault("CERS_COOKIE_SECURE", "0")
# Call state is in-memory; the single-worker lock would block repeated TestClient
# lifespans in one pytest process.
os.environ.setdefault("CERS_ALLOW_MULTI_WORKER", "1")


@pytest.fixture(autouse=True)
def isolated_backend(monkeypatch, tmp_path):
    """Keep every test offline and out of the real data directory.

    Without this, tests performed live Supabase DNS lookups and wrote fixture
    recordings into ``backend/recordings`` (a mock entry once leaked into the
    officer vault). Rate-limiter state is also reset so suites that log in
    repeatedly do not trip the production limits.
    """
    from backend.app import metrics as metrics_module
    from backend.app import providers, recordings, security

    metrics_module.metrics.reset()
    providers.registry.reset()

    monkeypatch.setattr(recordings, "SUPABASE_URL", "")
    monkeypatch.setattr(recordings, "SUPABASE_KEY", "")
    monkeypatch.setattr(recordings, "RECORDINGS_DIR", tmp_path)
    recordings._sync_state["backoff"] = recordings.SYNC_MIN_BACKOFF
    recordings._sync_state["last_attempt"] = 0.0

    for limiter in (
        security.call_creation_limiter,
        security.login_limiter,
        security.login_identity_limiter,
        security.triage_limiter,
    ):
        limiter._events.clear()

    yield
