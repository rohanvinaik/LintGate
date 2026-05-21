"""Oracle request types for the platonic workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class OracleRequest:
    """A request for human/agent oracle input."""

    function_key: str
    category: str  # e.g. "VALUE", "BOUNDARY"
    mutation_diff: str = ""
    required_oracle_type: Literal["value", "boundary", "property"] = "value"
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "function_key": self.function_key,
            "category": self.category,
            "mutation_diff": self.mutation_diff,
            "required_oracle_type": self.required_oracle_type,
            "context": self.context,
        }
