"""Pydantic request models shared by the HTTP API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreateCallRequest(BaseModel):
    dialed_number: str = Field(default="2007", min_length=1, max_length=16)


class LoginRequest(BaseModel):
    token: str = Field(min_length=1, max_length=256)
    officer_id: str = Field(default="OFFICER_01", min_length=1, max_length=64)


class NotesRequest(BaseModel):
    notes: str = Field(default="", max_length=10_000)
