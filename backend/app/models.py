"""Pydantic request models shared by the HTTP API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreateCallRequest(BaseModel):
    dialed_number: str = Field(default="2007", min_length=1, max_length=16)
    # Optional real caller identity (ANI/MSISDN) when telephony provides one.
    caller_number: str = Field(default="", max_length=20)


class LoginRequest(BaseModel):
    token: str = Field(min_length=1, max_length=256)
    officer_id: str = Field(default="Officer", min_length=1, max_length=64)


class NotesRequest(BaseModel):
    notes: str = Field(default="", max_length=10_000)
    disposition: str = Field(default="", max_length=32)
