"""Pytest configuration: set deterministic auth values before app imports."""

from __future__ import annotations

import os

os.environ.setdefault("CERS_OFFICER_TOKEN", "test-officer-token-0123456789abcdef")
os.environ.setdefault("CERS_SECRET_KEY", "test-session-secret-0123456789abcdef")
os.environ.setdefault("CERS_COOKIE_SECURE", "0")
