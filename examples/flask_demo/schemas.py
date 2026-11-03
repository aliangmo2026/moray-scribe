"""Pydantic request/response schemas (not ORM models)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginForm(BaseModel):
    """Credentials submitted by the login form."""

    email: str = Field(min_length=3)
    password: str = Field(min_length=1)


class OrderForm(BaseModel):
    """Payload accepted when creating an order."""

    user_id: int
    items: list[str] = Field(default_factory=list)
