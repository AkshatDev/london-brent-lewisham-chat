"""The shapes of the JSON the API sends and receives."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class Health(BaseModel):
    """Response body of the health check."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    status: Literal["ok"]
    """Always "ok" while the service is running."""
