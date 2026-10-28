"""Shared Pydantic base model for every moray-scribe schema."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Closed-world base model.

    All cross-layer payloads inherit from this class. Unknown fields are rejected
    so a malformed model response or an accidental ``**kwargs`` cannot smuggle
    data past the boundary, and assignment is validated to keep long-lived graph
    objects honest after construction.
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
    )
